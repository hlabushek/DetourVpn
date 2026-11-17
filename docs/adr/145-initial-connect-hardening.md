# ADR-145 — Initial connect hardening (connection-survivability Phase 2)

**Status:** implemented on branch `claude/connection-reliability` as Phase 2 of the owner-approved
connection-survivability plan; committed locally only, **not verified on a device**.
**Builds on:** ADR-119 (revoke wait), ADR-124 (user intent), ADR-139/140 (retry pace), ADR-144
(native state, single recovery owner).
**Supersedes in part:** the post-connect battery prompt in `ClientActivity.askBatteryExemption`
(at most twice, after the tunnel is up) and the order in which `_connect` asked for VPN consent (after device registration and
reconcile).

## Problem
A code audit (plan section G) found five faults on the first connect:
1. VPN consent came after device registration and reconcile, so a refusal still registered the
   device and touched server rows. `POST_NOTIFICATIONS` was never requested on Android 13+, and the
   battery exemption was asked only after a successful connect.
2. A failed `snapshot()` in `_reconcile` became `live = null`, which counted as "nothing running"
   and called `native.disconnect()` on a tunnel that might be up.
3. If the node list failed to load at startup, the saved node was dropped (`selectNode` ignores ids
   not in the list), `window_node_required` followed, and as a non-retryable code it called
   `_endUserIntent()`, silently clearing the user's "stay connected" wish.
4. A tunnel that never got a handshake ended much later as `lease_expired`, with a battery hint
   that was wrong for this cause.
5. While a server revoke was pending, the primary button was disabled with no way out, and every
   poll ran `_cleanup()`, which called `native.disconnect()` again each time.

## Decision
1. **Consent first (2.1).** `_connect` checks the selected node (no I/O), then calls
   `native.permission()` before any API call; the panel path no longer asks separately. On Android
   the `permission` channel method is the whole consent step:
   VPN consent (`VpnService.prepare`) → once, on Android 13+, `POST_NOTIFICATIONS` (new manifest
   permission) → once per install, a Russian explanation dialog «Работа в фоне», then Android's own
   `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` screen. Only the VPN consent decides the answer; a denied
   notification or battery request never blocks a connect. The "asked once" decisions are pure Java
   (`ConnectPrompts`). Phones that already saw the old post-connect prompt
   (`battery_prompted` ≥ 1) are not asked again. The post-connect prompt is removed.
2. **Unknown native state (2.2).** `_reconcile` retries `snapshot()` (3 attempts, 500 ms apart).
   If it still fails, it throws the new code `native_state_unknown`, before any disconnect or API
   call, and stays unsynced. `_connect` then skips the native stop in its cleanup, keeps the intent
   and schedules a retry; `_refresh` records the code and lets the next poll reconcile again.
3. **Intent survives missing nodes (2.3).** `main.dart` hands the saved node to
   `preferNode()`, which keeps it until a node refresh lists it. `window_node_required` never ends
   the intent. While a node may still appear (a saved node, an empty list or a node-list error),
   the failure is retried with the ADR-140 pace, each attempt refreshing the node list first and
   connecting only once a node is selected. With a loaded list and nothing chosen, it is a plain
   message and nothing retries. **Native mapping reviewed and kept:** `ConnectionSupervisor.category`
   maps `window_node_required` to the terminal `invalid_config`. Native only sees that code from the
   headless restore engine when no node was ever saved, which no retry can fix; a saved node missing
   from the list arrives as `window_node_unavailable` (`server_unreachable`, transient), and a failed
   node list as `node_validation_failed` (transient).
4. **`handshake_not_established` (2.4).** A window this controller started with `connectWindow`
   must show handshake evidence (`handshakeAge ≥ 0`, native `handshakeSeen`, a confirmed handshake,
   or native state `connected`) within **20 s** (`handshakeTimeout`). Otherwise the refresh fails
   with `handshake_not_established`: native is stopped and the lease revoked, then exactly one
   automatic attempt runs on a fresh lease. A second miss is terminal (the intent ends) with a clear
   Russian message; a user tap or any handshake resets the one retry. Adopted tunnels are exempt.
   The diagnostic hint is `server_busy` («сервер временно занят или недоступен»), not the battery
   hint. **Thresholds:** native keeps its display rule (`connecting/awaiting_handshake` for 30 s,
   then `recovering/no_handshake`, ADR-144). Dart's 20 s is the action threshold for UI-started
   windows and fires first, so in the foreground the UI acts before native would show
   `no_handshake`; native's 30 s remains the display for tunnels Dart is not watching (background,
   restore). No native code changed for this.
5. **Revoke pending (2.5).** While `remotePending` is set the primary action is «Повторить»
   (`retryRevoke`: one server revoke now) instead of a disabled button, or «Отменить подключение»
   while a connect still runs. The revoke notice adds «Сбросить» (`forceReset`): it ends the user
   intent, makes sure native is stopped, pauses polling and forgets the local hold, so Connect and
   server choice work again. It does not forget the server row: the controller is unsynced, so the
   next connect reconciles, revokes the old row first and refuses to create a new connection
   (`revoke_unconfirmed`) while the server still holds it. Revoke retries (polls, `retryRevoke`,
   the ADR-119 wait loop, reconcile's revoke of stale rows) no longer call `native.disconnect()`;
   native is stopped once, when the revoke starts. The revoke state is shown from `remotePending`
   with its own actions, so an earlier error code (`error ??= 'revoke_unconfirmed'` keeps the first
   cause) no longer hides it.

## Assumptions
- `VpnService.prepare()` returning null means consent is held, so asking first costs nothing for a
  connect that then adopts a running tunnel.
- A VPN service runs without `POST_NOTIFICATIONS`; a denial only hides its notification.
- 20 s is enough for a first handshake on a working path (WireGuard retries the initiation every
  5 s); the plan sets this value.
- After a refused consent, a stale server row is not revoked in that attempt; it ends at its window
  deadline (≤ 900 s, no grace) or is revoked by the next reconcile.

## Security and privacy
- One new permission (`POST_NOTIFICATIONS`), requested at runtime once; no new network call,
  endpoint, exported component or stored secret. Two new preference flags hold only booleans/counts.
- A refused VPN consent now makes no API call: no device registration and no server change.
- «Сбросить» is local only. The server stays authoritative: a new connection still requires the old
  one to be closed (reconcile + server refusal), so it cannot be used to hold two connections or to
  dodge quota settlement.
- `native_state_unknown` never stops the tunnel. Fail-closed limits remain the native deadline and
  Android Lockdown (ADR-132), unchanged.
- New error codes are fixed strings; both are added to the Dart and Java diagnostic allow-lists
  (sync test). No identifier, address or key enters a message or report.

## Known limitations
- **Not verified on a device.** No APK was built and nothing was installed.
- Play policy for asking `POST_NOTIFICATIONS` and the battery exemption at first connect was not
  reviewed; `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` itself predates this ADR.
- The consent step's dialogs run inside `ClientActivity`; an Activity recreated during them answers
  nothing (as before), and the connect then waits until the user taps again.
- The 20 s rule acts only while the UI controller polls (foreground). A first connect that loses its
  handshake while the app is in the background is left to native (Phase 5).
- The rule also applies on Windows and in the headless restore engine (both start windows with
  `connectWindow`); this is not separately verified there.
- A node list that never loads keeps a slow node-list retry (about every 60 s at the ADR-140 pace)
  until the user taps or disconnects; it reserves no quota.

## Verification
- Dart: `flutter test` from `apps/client` with the pinned SDK — **310 passed** (295 before). New
  file `test/initial_connect_test.dart` (13): consent before any API call; a refused consent makes
  no API call; a once-failing snapshot is retried; a failing snapshot stops nothing and keeps the
  intent, then connects; a refresh with an unknown state stops nothing; a failed node list keeps the
  saved node and connects after the next refresh without ending the intent; no node to wait for
  gives a message and no retries; no handshake → one fresh-lease retry → terminal; a handshake in
  time clears the rule; a pending revoke does not stop native per poll; «Повторить» settles it;
  «Сбросить» frees the UI while the next connect still revokes first; the screen shows both actions.
  `primary_action_test` updated (retry instead of a disabled action) and the ADR-121 restore test
  «old remote lease is closed before permission denial» rewritten for the consent-first order.
- `flutter analyze` on an ASCII-path copy (lib, test, pubspec, analysis options): no code issues;
  one expected warning that `assets/orbit/` was not copied.
- **Deviation from the required check:** `gradlew testDebugUnitTest` was **not** run (no Gradle
  wrapper in this worktree). Substitute, as for ADR-140..144: all app Java sources (client and shared
  trees, patched tunnel sources, generated registrant stubbed) compile with `javac --release 17`
  against Android SDK 36; JUnit 4.13.2 — **187 tests passed** (185 before; new `ConnectPromptsTest`
  2), with `RestartInPlacePatchTest` run from the `apps/client/android/app` layout.
- No device, server, Gradle build or release script was touched.

## Owner phone steps (NOT VERIFIED — build from this branch)
1. Fresh install on Android 13+: tap «Подключить». Expect, in order, the VPN consent, the
   notification prompt, the «Работа в фоне» explanation and Android's battery dialog, before any
   connecting status. Deny the notification: the VPN still connects.
2. Deny the VPN consent: «Разрешение VPN не выдано», and no new device row on the server.
3. Airplane mode, open the app (node list fails), turn the network on: it connects by itself.
4. A node that drops UDP: after about 20 s one automatic retry, then «Сервер не ответил на
   подключение…».

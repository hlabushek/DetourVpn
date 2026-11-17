# ADR-144 — Native connection state, event log and a single recovery owner

**Status:** accepted by the owner 2026-11-17 as Phases 0 and 1 of the connection-survivability plan;
implemented on branch `claude/connection-reliability`, **not committed, not verified on a device**.
**Builds on:** ADR-121 (restore hand-over), ADR-124 (supervisor and user intent), ADR-128/133
(health evidence, data-path stall), ADR-142 (recovery owner follows the Activity), ADR-143 (fresh
wake and network signals).
**Supersedes in part:** ADR-142's rule that a backgrounded UI controller records a refresh failure
and cleans up (stop + revoke) before deferring the reconnect (see "Background UI").

## Problem
Three owners act on one connection: the Flutter UI controller, a headless Flutter restore engine
and native Java (`ClientApplication`, `ConnectionSupervisor`, `NativeRenewer`, `TunnelWatchdog`).
A code audit (owner-approved plan, 2026-11-17) found these concrete faults:

- **Wrong status.** Flutter decided «Подключено» vs «Восстановление связи» from its own 2 s poll,
  with a handshake at most **60 s** old (`NativeSnapshot.confirmed`). WireGuard rekeys about every
  120 s and rejects a session after 180 s, so an idle, working tunnel flipped to «Восстановление
  связи». Native already had the correct 180 s rule (`ConnectionSupervisor.transport`), unused.
  An unreachable API (`network_unavailable`, `api_unavailable`, `rate_limited`) also forced
  «Восстановление связи» on a healthy tunnel, and adopting a running tunnel always began in it.
- **Background UI could kill native recovery.** The UI kept polling in the background. A failed
  poll there called `_cleanup()`: `native.disconnect()` plus a server DELETE, stopping the tunnel
  native was restarting or had restored.
- **R3:** `_adopt` ended the restore engine only when native renewal was off. An engine that had
  already handed renewal to native stayed alive; `restoreActive` stayed true, so native recovery
  was disabled for good and two Dart controllers polled at once.
- **R4:** a restore engine whose tunnel later failed (in Doze its Dart timers sleep) still counted
  as the recovery owner, so nobody recovered. Its terminal codes never reached native either
  (`setIntent` is not on the restore channel).
- **R5:** on a cold start the network callback could start native recovery before
  `ClientActivity.onStart` said the UI is open, racing the UI's own reconnect.
- **R7:** a VPN service that died on its own (not revoked, not stopped by us) was only noticed when
  the window deadline passed, up to 15 min later.
- **R8:** the UI connect path skipped the CPU lock, the handshake-memory reset and the failure-stage
  reset that the restore path did.
- **No evidence.** After a failed night nothing on the phone said what happened (ADR-142 had to infer
  the cause).

## Decision
### Phase 0 — observability (no behaviour change)
1. `ConnectionEventLog` (pure Java): an in-memory ring buffer of 200 entries. An entry is an
   elapsed-time stamp, an event code and an optional detail code, both restricted to
   `[a-z0-9_]{1,48}`; anything else is stored as `invalid`. No address, endpoint, key, identifier or
   free text can enter it. It is never written to disk.
2. Recorded events: process start, owner changes (`owner ui|native`, UI engine attach/detach),
   network available/usable/lost/address change, recovery start/refusal, alarm wake-ups, fresh
   signals, stops with their reason, watchdog restarts, window up/advanced, native renewal success
   and stop reason, restore engine start/up/failed/ended/adopted, user disconnect, VPN revoke,
   and every change of the connection state.
3. Export: the last 80 entries, as ages in seconds (`-123s stopped lease_expired`), with the current
   state, are appended to the user-shared diagnostic report (`shareDiagnosticReport`). A
   debuggable-only channel method `debugNativeState` returns the same text for a test harness.

### Phase 1 — native owns the state; Flutter renders it
1. **`ConnectionState`** (pure Java, one place): `disconnected`, `connecting`, `connected`,
   `recovering` (the spec's "reconnecting"), `failed`, each with a reason code. While the tunnel is
   up: no usable network → `recovering/waiting_for_network`; a pending post-switch check →
   `recovering/network_changed`; a data stall (ADR-133) → `recovering/data_stalled`; a handshake at
   most **180 s** old and newer than the last network change, or inbound bytes flowing with no
   network change in the last 60 s → `connected/verified`; no handshake yet within 30 s of the
   window start → `connecting/awaiting_handshake`, after that `recovering/no_handshake`; otherwise
   `recovering/handshake_stale`. While down: wanted → `recovering/<restoring|waiting_for_network|
   stop category>`; not wanted → `failed/<category>` after a terminal non-user stop, else
   `disconnected`.
2. **Push channel.** `EventChannel('project.vpn/state.v1')` on the UI engine only. Native sends
   `{state, reason, desired, restoreActive, nativeRenewal, up, connection, windowGeneration}`
   (codes and the fenced ids the app already holds) whenever the state, the held connection or
   these flags change, and once on subscribe. Changes are checked after every recovery decision,
   network event, watch tick (15 s while up), window start/advance, stop and user disconnect.
   `snapshot()` also returns `state` and `stateReason`.
3. **Data-path sampling and the Always-on reading** move to the native watch tick, so the stall
   evidence no longer depends on how often Flutter polls. `snapshot()` keeps its fail-closed
   deadline check.
4. **Flutter renders.** On Android, when native reports a state, the UI controller shows it
   (connected → «Подключено», connecting → «Подключение», recovering → «Восстановление связи» with
   a reason text) instead of its own rule. The Dart rule stays for Windows and older native builds.
   A transient API failure while native still runs the tunnel is a note
   («VPN работает, сервер временно недоступен», `lastApiError`), not an error state.
   «Подключено HH:MM:SS» is shown only under the «Подключено» title.
5. **Background UI** (Android UI controller). In the background it never stops the tunnel, revokes
   a connection, reconciles or reconnects. It keeps polling only while it renews the window itself
   (native renewal off); otherwise polling pauses and resumes in the foreground. A refresh failure in
   the background is remembered; in the foreground the controller drops its local lease view
   (no stop, no revoke) and reconciles with native: it adopts what native runs, or resumes.
6. **Native moved on.** If a snapshot shows native restoring (down, restore engine alive) or running
   a different connection than the one the UI holds, the UI drops its local view and waits/adopts;
   it never stops that tunnel.
7. **R3:** adoption ends a live restore engine whenever one runs. Dart takes over renewal (and
   applies a one-ahead generation) only when native does not renew.
8. **R4:** a restore engine owns recovery only while it boots its first tunnel (bounded by
   180 s; a timeout counts as a failed bootstrap) or while its tunnel is up
   (`ConnectionSupervisor.restoreEngineOwnsRecovery`). When its tunnel stops after bootstrap (native
   stop, or the engine's own `disconnect`, recorded as `restore_stopped`), native ends the engine and
   recovers itself with the normal backoff. A failed engine start now re-evaluates recovery.
9. **R5:** after process start native decides recovery only after the UI's first owner signal
   (`onStart`/`onStop`) or 3 s, whichever comes first. A recovery alarm that starts the process waits
   out that settle inside its broadcast. Boot and sticky restarts are unchanged.
10. **R7:** when the VPN service is destroyed while a window is still held and nothing here stopped it,
    native stops the window and records `service_died` (category `tun_failure`, transient), so
    recovery starts at once. The restore service's own unexpected death now also re-evaluates.
11. **R8:** UI and restore paths share `prepareWindow()` (reset handshake memory and data path before
    connect) and `beginWindow()` / `windowAdvanced()` (CPU lock, window start for the supervisor,
    restore wake, state push). The supervisor's up period now starts only once the tunnel is up.

## Security and privacy
- No new permission, network call, exported component or stored data. The event log is memory
  only; its codes are allow-listed, and the report shows ages, not clock times.
- The state stream carries only codes and the connection/generation ids the app already received
  from the API; no key, token, endpoint or address.
- The UI can no longer stop or revoke from the background, which removes a path where Flutter could
  cancel native's fail-closed recovery. Lockdown behaviour is unchanged (ADR-132).

## Known limitations
- **Not verified on a device.** Every claim above is from code reading plus JVM and Dart unit tests.
- Ending a restore engine whose tunnel stopped also stops its VPN service before native starts a new
  one, so an awake phone briefly has no VPN service where the old engine would have reconnected in
  place. Under Lockdown traffic is blocked in that gap.
- The 3 s startup settle delays native recovery after a process start by up to 3 s.
- `isLockdownEnabled()` is still read before `establish()` (R6, plan Phase 6). A failed snapshot in
  the UI's reconcile still counts as "nothing running" (plan Phase 2.2).
- The pushed state is advisory for rendering; the UI still polls `snapshot()` for renewal and
  reconcile. Windows keeps the old Dart status rule.

## Verification
- Dart: `flutter test` — **295 passed** (new: idle tunnel at 150 s shows «Подключено»; recovering
  reasons; an unreachable API keeps «Подключено»; a pushed event updates the status; polling pauses
  and resumes; Dart keeps polling while it renews; a tunnel native replaced is not stopped; a restore
  in progress is waited for; R3 hand-over with native renewal. The ADR-142 background test was
  rewritten for the new rule).
- `flutter analyze`: crashes on the Cyrillic OneDrive path (known environment issue). On an
  ASCII-path copy of `apps/client` (lib, test, pubspec, analysis options): no code issues; one
  warning that `assets/orbit/` was not copied.
- **Deviation from the required check:** `gradlew testDebugUnitTest` was **not** run (this worktree
  has no Gradle wrapper). The javac + JUnit run below is a substitute and is not a Gradle pass.
- Java: no Gradle wrapper in this worktree, so, as for ADR-140..143: all app Java sources (client
  and shared trees, patched tunnel sources, generated plugin registrant stubbed) compile with
  `javac --release 17` against Android SDK 36; JUnit 4.13.2 — **185 tests passed**
  (`ConnectionStateTest` 9, `ConnectionEventLogTest` 3, `ConnectionSupervisorTest` 35 incl. 3 new;
  `RestartInPlacePatchTest` run from `apps/client/android/app`, as Gradle does).
- No APK built, nothing installed, no device or server touched.

## Owner phone steps (NOT VERIFIED — build from this branch)
Record only pass/fail, the status text and the event log from the diagnostic report.
1. Connect, leave the phone idle with the app open for 5 min: the title stays «Подключено».
2. Connect, turn the screen off overnight; in the morning open the app and share the diagnostic
   report: the event log shows each renewal or stop and recovery.
3. Connect, switch Wi-Fi off and on: «Восстановление связи» with «Сеть сменилась…», then «Подключено».
4. Force-stop is not a test (Android forbids restarts after it).

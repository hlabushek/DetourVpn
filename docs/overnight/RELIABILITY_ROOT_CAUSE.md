# Connection reliability — root cause from code (2026-11-11)

Line numbers refer to `ffdae8f` (before this branch's changes). Labels:
- **Verified in code**: the code path exists and a unit test reproduces it (this branch).
- **Hypothesis**: plausible, but it depends on device or server timing that is not in the repo.

Nothing here was verified on a phone, a node or the beta server.

## 0. What the diagnostic line means
`diagnosticStage = 'lease_validation'` is set in one place only, in `_connect`, right before the
*first* validation of a new connection (`apps/client/lib/window_connection.dart:320-321`). After a
successful connect the stage is reset to `null` (`:344`), and `_refresh` never sets it. So
`window_not_active` + `lease_validation` comes from a **connect attempt** (manual or automatic),
not from a renewal during a session.

`_validate` throws `window_not_active` (`:433-440`) when:
- `state != 'active'` after the pending loop (`:311-318` exits on any non-`pending` state), or
- `client_config` is missing or over 16 KiB, or `id`, `device_id`, `meter` differ.

The server (`services/api/src/vpn_api/tunnels/window_routes.py:105-127`, `view`) returns:
- `client_config = None` for an `active` connection when the node's heartbeat is older than 120 s,
  the node runtime is not ready, or the runtime changed (`:112-117`);
- `revoke_pending` when the grant is no longer authorized (`:108-110`).

## 1. When a window stops being active on the server
| Cause | Where | Effect |
|---|---|---|
| End of window | `window_service.py:383` (`grant_authorized`: `not_after <= now + 5 s`) | next node poll or user view marks `revoke_pending` (`:622-627`, routes `:108-110`) |
| Late renewal | `window_service.py:491-503` | `window_not_renewable` (409) when state is not active, window id moved, or the old grant is inside the 5 s margin |
| Node contract margin | `v3_contracts.py:30,107` (`RENEWAL_MARGIN_SECONDS = 5`, `renewal_too_late`) | node refuses a renewal grant inside the margin |
| Node gate | `window_runtime.py:267` (gate opens until `not_after - 6 s`), `:339-343` (`expire`) | node closes and removes the peer at `not_after`, then sends a receipt; server closes the connection (`window_service.py:585-593`) |
| Node lost an active peer | `window_service.py:632-635` | an `active` connection not reported by the node becomes `revoke_pending` |
| Node restart (new runtime) | `window_service.py:622-625` | `candidate.runtime_id != body.runtime_id` → `revoke_pending` |
| User or other engine revoke | `window_routes.py:328-339` (`DELETE`), client `_reconcile` `window_connection.dart:224-242` | `revoke_pending`, then `closed` after the node receipt |
| Funding or account | `window_service.py:376-402` | user inactive, device revoked, or funding lot revoked or expired → `revoke_pending` |

There is no grace after `not_after`, by design: the node already removed the peer. The margin is
5 s in two places; ADR-120 makes it one constant.

## 2. Symptom 1, part A — the app stops its own VPN when it is reopened (**verified in code**)
1. ADR-097 keeps the tunnel and its renewal running in the app process after the UI engine dies.
2. `ClientActivity` is a plain `FlutterActivity` (`ClientActivity.java:24`), so its engine is
   destroyed with the activity (swipe from recents, or Android reclaiming a long-backgrounded activity).
3. On the next launch `signedIn()` (`main.dart:167-190`) calls `onAuthenticatedSessionChanged()`
   (`_synced = false`), then `_initializeWindowController` calls `refresh()` (`main.dart:210`).
4. `refresh()` → `_reconcile()` → **`native.disconnect()`** (`window_connection.dart:227`), then
   `DELETE` for every non-closed connection (`:233-239`). The channel `disconnect` also clears
   `resume_connected` (`ClientActivity.java:209-212`).

Result: the VPN goes off when the user opens the app after it was closed for a while. To the owner
this looks like "the VPN disconnected on its own". Test: `ADR-117 a reopened UI adopts ...`.

## 3. Symptom 1, part B — Doze leaves room for about one renewal attempt (**hypothesis, strong**)
- `NativeRenewer` attempts once per wake-up (`NativeRenewer.java:277-338`). On failure it calls
  `retry()` (`:344-347`): an in-process timer plus `setAndAllowWhileIdle` (`:355-365`), then the
  60 s wake lock is released (`:249-257`).
- In Doze the CPU sleeps, so the in-process timer does not advance. Android documents that
  allow-while-idle alarms fire at most about once per 9 minutes per app in Doze.
- A 900 s window starts renewing with 10 minutes left (`NativeRenewal.java:47`). In deep Doze that
  leaves room for about one attempt. One transient failure at wake-up, or a node acknowledgement
  slower than the 30 s pending poll (`NativeRenewer.java:173-174, 305-329`), lets the window run out.
- The native deadline then stops the tunnel (`ClientApplication.java:103-109`), and the server
  revokes the connection (§1).

Not verified on a device: actual Doze alarm timing on the owner's phone, and OEM battery managers
(which can ignore the battery exemption). The battery exemption allows network access in Doze but
does not lift the alarm rate limit.

## 4. Symptom 1, part C — the restore path always fails with 900 s windows (**verified in code**)
- If Android kills the process, the sticky service restarts (ADR-098) and runs the headless Dart
  restore (`always_on_recovery.dart:66-86`). Boot restore uses the same path.
- After `connectWindow`, `_startNativeRenewal` calls `startNativeRenewal`. The restore channel
  handler does not list it (`ClientApplication.java:229-233`) and answers `notImplemented`.
- Flutter raises `MissingPluginException`, which is neither `ClientError` (the only type caught at
  `window_connection.dart:513`) nor `PlatformException`. `_connect` fails with
  `native_unavailable` (`:350-358`), cleans up, and the restore reports `bootstrapFailed`, which
  stops the service.
- So after a process kill, the VPN comes back for a moment and then stops. The 90 s default
  build would not hit this (no native renewal); release builds use 900 s
  (`scripts/release_all.ps1:99`, `scripts/enable_app_update.ps1:80`).
- Also, the restore path renews from Dart timers in a headless engine, so Doze ends it anyway
  (ADR-098 limits). That part is not changed here.

## 5. Symptoms 2 and 3 — connect or switch fails, a retry works
**Verified in code:**
- `selectNode` ignores a choice while `hasLease || busy` (`connection.dart:244-246`). The region
  picker closes as if the choice was taken. Picking a server while connected or connecting does
  nothing, and a later connect goes to the old server.
- After a disconnect, the old connection stays `revoke_pending` until the node confirms. The next
  connect calls `_cleanup()` once and throws `revoke_unconfirmed` if it is still pending
  (`window_connection.dart:279-283`). A second tap a few seconds later works.
- A refresh that fails with `window_not_active` or `lease_expired` schedules automatic reconnect
  only if `!hasLease` (`:774-775`). With a real server, `DELETE` answers `revoke_pending`, so the
  reconnect is never scheduled; the remote cleanup later finishes (`:616-619`) and nothing follows.
  The Russian text (`connection.dart:25-26`) still says the app is retrying.
- An automatic reconnect that hits a running refresh returns early (`window_connection.dart:246`),
  and the retry is lost.

**Hypothesis for `window_not_active` at `lease_validation`:** a second controller revoked the new
connection while it was pending. Two engines can run Dart controllers at once: the UI and the
headless restore (§4). Each one's first `_reconcile()` revokes every non-closed connection of the
device, including the other's pending one. Then the pending loop sees `revoke_pending` and
`_validate` throws `window_not_active`. A retry works because by then the other engine has given
up. Other possible causes, which need server data (`RELIABILITY_OWNER_LOGS.md`): the node restarted
between create and acknowledgement (new runtime), the node heartbeat was older than 120 s when the
connection turned active (no config), or a funding lot expired seconds after admission.

## 6. What was not found
- No client or server path that renews a window after `not_after`.
- No evidence in the repo of what the owner's phone did (no device log, no server history). The
  owner steps in `RELIABILITY_OWNER_LOGS.md` collect that.

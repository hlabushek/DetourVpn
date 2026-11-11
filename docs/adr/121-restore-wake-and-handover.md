# ADR-121 — Restored VPN: Doze-safe wake-ups, and hand-over to the app UI

**Status:** proposed 2026-11-11 (connection reliability, follow-up to ADR-117/118).
**Amends:** ADR-098 (service restore), ADR-117.

## Context
After a reboot or a process kill, the VPN is restored by a headless Dart engine (ADR-098). That
engine renews the window from Dart timers and reaches the API through `ProtectedApiRelay` (the
app's own traffic bypasses its VPN, and under Lockdown only protected sockets get out).
Two problems are left:
1. **Doze.** Nothing wakes the CPU for the headless Dart timers, so the 15-minute window can run
   out while the phone sleeps.
2. **Opening the app kills the restored VPN.** ADR-117 adopts a running tunnel only when native
   renewal runs. A restored tunnel renews from the headless engine, so the new UI stops it and
   revokes the connection, while the headless controller keeps running and tries to reconnect.
   Two controllers that each clean up "all connections of this device" are also the main suspect
   for `window_not_active` at `lease_validation` (`RELIABILITY_ROOT_CAUSE.md` §5).

## Options considered
- Native renewal in restore mode through the relay: `HttpsURLConnection` cannot be routed through
  the relay without a hand-written HTTP/TLS client. Rejected (new security-sensitive code).
- `VpnService.protect()` for the native renewer's sockets: same problem, `HttpsURLConnection`
  gives no hook for its raw socket. Rejected.
- **Wake the existing headless Dart controller with an allow-while-idle alarm**, and **hand the
  tunnel over** to the UI when it starts. No new network code. Chosen.

## Decision
### Wake-ups in restore mode
- After a restore `connectWindow` or `advanceWindow`, native schedules an
  `AlarmManager.setAndAllowWhileIdle` alarm (action `WAKE_RESTORE`, not exported, no data).
  The delay comes from the pure `NativeRenewal.restoreWakeDelayMs(remainingMs)`: every 4 minutes,
  and never later than 6 s before the native deadline; no alarm once less than 1 s would be left
  before that margin. In Doze, Android stretches this to its own limit (about 9 minutes).
- On the alarm, native holds a 45 s partial wake lock and calls `wake` on the restore engine's
  channel. Dart runs `refresh()`. Its normal renewal and retry logic runs while the CPU is awake.
  The next alarm is scheduled. Any stop or detach cancels the alarm.

### Hand-over to the UI
- The native snapshot reports `restoreActive` (a restore engine is alive).
- A UI controller that starts while the tunnel is up and `restoreActive` is true validates the
  server's active connection with the ADR-117 checks first. Then it calls `adoptRestore`.
- `adoptRestore` (UI channel only) destroys the restore engine and closes its relay **without**
  stopping the tunnel or the VPN service, and returns whether the tunnel is still up.
- If the server is exactly one generation ahead of native (a renewal finished during the
  hand-over), the UI applies it with `advanceWindow` under the usual fence. If it is further ahead,
  the UI does not adopt and falls back to stop and revoke.
- The UI then starts ADR-097 native renewal for the adopted connection. If that fails, the UI's own
  Dart renewal continues.

## Security considerations
- No new endpoint, credential, permission or network path. The alarm carries no data, and its
  receiver is not exported.
- Hand-over requires the same server-side match as ADR-117 (device, key, connection, runtime,
  generation). Any mismatch falls back to the fail-closed stop and revoke.
- Only the UI channel can call `adoptRestore`; the restore engine's channel does not offer it.
- After a hand-over, calls still arriving from the old restore engine are refused, so it cannot stop
  or move the tunnel the UI now owns.

## Limits / not verified
- Not verified on a physical device. Doze stretches the wake-ups, and some OEM managers may block
  them.
- **Lockdown.** The native renewer cannot reach the API under Lockdown (its sockets are not
  protected), and while it runs Dart does not renew. So native records Lockdown when an Always-on
  start reports it (`VpnService.isLockdownEnabled()`, Android 10+), and `startNativeRenewal`
  answers `false` then; Dart keeps renewing. This also covers normal UI connects. Lockdown that is
  switched on without a new Always-on start is not detected.
- Dart renewal in the UI (Lockdown, or a failed native start) is still exposed to Doze.

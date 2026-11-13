# Connection reliability run 3 — periodic work on the connected path (item F)

Plain English. **No battery measurement was made.** This is an inventory of timers, polls and alarms
found by reading the code at the start of run 3 (`0835dc4`, plus this run's changes). Intervals are the
values in the code. Nothing here is proven on a phone.

## Inventory

| # | Work | Where | Interval | Runs when | Decision |
|---|---|---|---|---|---|
| 1 | WireGuard persistent keepalive | profile `PersistentKeepalive` (25 s), upstream engine | 25 s | window up | **Kept.** Keeps the NAT mapping open and gives the watchdog handshakes (`TunnelWatchdog.STALE_MS` 180 s, `FRESH_MS` 60 s depend on it). No change without a transport reason. |
| 2 | WireGuard rekey | upstream engine (`rekey_after_time`, 120 s) | 120 s | window up | **Kept.** Same reason as 1; the 180 s stale rule assumes it. |
| 3 | Tunnel check (`watchTunnel`) | `ClientApplication` | 15 s | **was: whole process life; now: only while a window is up** | **Changed (ADR-135).** Re-armed by `setDeadline`; stops when no tunnel is up. Removes the wake-up while disconnected. |
| 4 | Pending network check | `schedulePendingWatch`, `TunnelWatchdog` | one-shot, settle 3 s, gap 15 s | a network change is pending | **Kept.** One-shot; only after a network change (ADR-122). |
| 5 | Window deadline check | `setDeadline`, `enforceDeadline` | one-shot, at the deadline | window up | **Kept.** Fail closed at the deadline (ADR-126). Not periodic. |
| 6 | Native renewal alarm | `NativeRenewer` (`setAndAllowWhileIdle`) | at `max(55 s, window − 300 s)` before expiry; burst retries 2 s, 4 s, 8 s | native renewal active | **Kept.** Renews the window in Doze; Android stretches it to about 9 min. |
| 7 | Restore wake alarm | `scheduleRestoreWake` (`NativeRenewal.RESTORE_WAKE_MS`) | every 4 min | restore engine alive only | **Kept (ADR-121).** Wakes the headless Dart engine to renew. Interval chosen so that the 900 s window has several chances even in Doze. |
| 8 | Recovery alarm | `scheduleRecoveryAlarm` | backoff 30 s → 30 min (min 60 s); refused start 60 s (ADR-134) | intent on, no tunnel | **Kept.** Only while the user wants the VPN and nothing runs. Stops when the user disconnects. |
| 9 | UI status poll | `ConnectionController`, `pollInterval` default 2 s | 2 s | UI engine attached | **Kept.** Drives the status text and renewal timing. Only while the UI is attached. |
| 10 | Headless restore poll | `always_on_recovery.dart`, `pollInterval` 3 s | 3 s | restore engine alive only | **Kept, candidate for later.** Renewal in Dart happens about 55 s before expiry, so a longer poll makes Dart renewal late. Changing it needs device test B first. |
| 11 | Dart one-shot timers | `window_connection.dart` (handshake hint 25 s, auto-reconnect 2 s and backoff) | one-shot | status or reconnect | **Kept.** One-shot, event-driven. |
| 12 | Network callbacks | `ClientApplication.watchNetwork` | event-driven | Android network change | **Kept.** No polling. |
| 13 | UI animation (orbit backdrop) | Flutter UI | continuous while the screen is shown | screen on | **Not changed.** It runs only while the UI is visible; a change here is a UI decision for the owner. |

## Changes made in run 3
- **Item 3:** the 15 s tunnel check no longer runs while disconnected (ADR-135). Effect: no periodic
  process wake-up while no window is up. **Not measured.**
- Data-path evidence (ADR-133) and the restore/refusal rules (ADR-134) add no timer.

## Not changed, and why
- Keepalive and rekey intervals: the stall threshold (ADR-133), the watchdog stale rule and the freshness
  rule all depend on them. Any change needs a transport reason and a device test.
- The 3 s restore poll and the 4 min restore wake: changing them changes when Dart renews. Device test B
  must show the current values first.

## How to measure (owner, on a phone; not done here)
1. Charge the phone, then run `adb shell dumpsys batterystats --reset`.
2. Connect with the signed build, keep the screen off for 60 minutes, then disconnect.
3. Run `adb shell dumpsys batterystats` and note the app's wakeup count and CPU time. Compare with the
   run-2 build (`144080f`) under the same conditions.
Report the numbers as measured on a named device and Android version; do not extrapolate.

# A2 — Review of ADR-144..154 (state, timers, restarts), 2026-11-27

Scope: commits `1fdbbf4` (ADR-144) to `612ac1a` (ADR-154) on `claude/connection-reliability`,
read at `d6f9cb6`. Focus as planned: connection state, timers and restarts. Read in full:
`ConnectionState`, `TunnelWatchdog`, `DeadPeerWatch`, `DataPathWatch`, `TunnelProbeRunner`,
`NetworkCallbacks`; in `ClientApplication`: `applyNetwork`, `networkPathChanged`,
`applyUnderlying`, `watchTunnel`, `restartTunnel`, `giveUpWindow`, `startProbe`,
`probeFinished`, `escalate`, `scheduleWatch`, `startAlwaysOnRecovery`, `forgetWindowWatch`,
`renewalView`; the ADR-152 (`NativeRenewer`, `NativeRenewal`, `ClientActivity` apps executor)
and ADR-154 (`WindowConnectionController.connect`) diffs.
Not read line by line: `DozeSurvival`, `BackstopJobService`, `BootReceiver`, `RestoreTrigger`,
`RecoveryCompanionService`, `ConnectionEventLog`, and most of the 500-line `window_connection.dart`
diff. Treat those as **not reviewed**.

## Result

No clear defect was found that a focused unit test could prove, so **no code was changed** in
A2. The items below are recommendations for the owner, ordered by severity. Line numbers are at
`d6f9cb6`, paths relative to `apps/client/android/app/src/main/java/org/example/vpnpoc/` unless
stated.

| # | Severity | Where | Finding | Recommendation |
|---|---|---|---|---|
| 1 | Medium (feature is inert) | `TunnelProbeRunner.java:63-76`, `DeadPeerWatch.java:136-138` | The ADR-148 probe can probably never run on a phone: the app excludes its own package from its VPN (ADR-132), and Android normally refuses to bind a socket of an excluded UID to the VPN network, so every probe would end as `bind_refused` (UNAVAILABLE). The URL is also still the placeholder `none`. Harmless today (the ladder is off and the watchdog behaves as before), but the whole ladder is dead code in production. | Owner decision, see A3 (ADR-198). Device check in A5. |
| 2 | Low | `TunnelWatchdog.java:51-55` with `ClientApplication.java` `forgetWindowWatch()` (ADR-153) | `tunnelDown()` clears start time, pending trigger and network-restart count, but not `lastRestart`. A new window that starts within 15 s of the old window's last restart waits for the rest of that 15 s gap before its first network-change restart. ADR-153 says the watchdog forgets "restart budget"; `lastRestart` is a separate anti-flap gap and may be intended. | Decide whether the gap should be per window. If yes: reset `lastRestart` in `tunnelUp()` and add one `TunnelWatchdogTest` case. Not changed overnight because it is a behaviour choice, not a proven bug. |
| 3 | Low | `ConnectionState.java:126-127` | "Handshake after the network change" compares whole seconds (`handshakeAgeSeconds * 1000`) with milliseconds. A handshake up to ~1 s *before* a change can count as "after" it, so the state can show «Подключено» up to one second early after a switch. The native counter is in seconds, so this cannot be exact. | Accept, or require `handshakeAgeSeconds * 1000 + 999 < networkChangeAgeMs` (conservative). Needs a short owner call; UI effect only. |
| 4 | Low | `window_connection.dart` `connect()` (ADR-154, about line 893-907) | After `await _refreshWork`, `connect()` relies on `refresh()`'s `finally` (which clears `_polling`) having run first. It does today, because `refresh()` subscribed to the same future earlier and Dart runs listeners in order. If the poll code is restructured, the tap would again be dropped silently. Also, an exception from the poll would propagate out of `connect()`. | Keep the existing ADR-154 test; consider awaiting a dedicated "poll finished" completer, or catching in `connect()`. Not a current bug. |
| 5 | Info | `TunnelProbeRunner.java:45-49` | The 5 s timeout task is never cancelled after an early result. It then only calls `disconnect()` on an already closed connection and a no-op `once`. Harmless. | None needed; optionally keep the `ScheduledFuture` and cancel it. |
| 6 | Info | `ClientApplication.java:1104-1109` | When a lost probe result escalates to RESTART inside `watchTunnel`, `restartTunnel()` already runs `applyUnderlying`, `schedulePendingWatch` and `publishState`, and `watchTunnel` then runs them again. Idempotent; no effect. | None. |
| 7 | Info | `ClientApplication.java:1119-1134` | Restart order (restart in place, then `setUnderlyingNetworks`) is reasoned but unverified on a device, as the in-code TODO says. | A4 adds a JVM order test of the source; device item in A5. |

## Checked and found consistent

- `DeadPeerWatch`: stale or lost probe results are ignored by id; `windowStarted()` keeps the
  ladder, `networkChanged()` and `reset()` clear it; UNAVAILABLE never escalates; arithmetic on
  `NEVER = Long.MIN_VALUE / 2` does not overflow for elapsed-realtime values.
- `DataPathWatch`: a counter reset (lower value after a restart in place) starts a fresh baseline.
- `TunnelWatchdog.decide`: pending network trigger survives an unusable network and the 15 s gap;
  `NEW_LEASE` after two unproven restarts; stale restarts are off only while the probe owns them.
- `NetworkCallbacks`: the loss timer is cancelled on a new `onAvailable` and re-armed on a
  repeated `onLost`; a late loss of an old network does nothing.
- ADR-152: only `TimeoutException` counts as "worker busy"; the queued read is cancelled.
- ADR-153: `forgetWindowWatch()` runs on `stop()` and `prepareWindow()`; `beginWindow()` starts
  the watchdog for the new window.

## Checks run

None new for A2 (review only). Baseline from A1 applies: `flutter test` 431 passed; Java
substitute 274 + 11 passed. Nothing here is verified on a device.

## Files changed in A2

- `docs/overnight/A2_REVIEW_ADR144_154.md` (this file).

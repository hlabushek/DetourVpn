# ADR-153 — The watchdog's state belongs to one window

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-099/122/146 (watchdog, network restarts, new-lease step), ADR-144 (shared
`prepareWindow` / `beginWindow`).

## Problem (tester report: rare random drops, second cause)
`TunnelWatchdog` keeps per-window state: `upSince`, the pending network trigger
(`networkChangedAt`), the restart budget (`networkRestarts`) and the stall backoff. Code reading:

- `ClientApplication.stop()` (user disconnect, every Dart cleanup, a server switch, the deadline)
  did not reset it, and neither did `prepareWindow()` nor `beginWindow()`. Only `watchTunnel()`
  reset it, on the next tick, when it saw no tunnel (`if (!up) { watchdog.tunnelDown(); ... }`), and
  `giveUpWindow`.
- `watchTunnel()` starts tracking a window only `if (!watchdog.running())`. A new window that started
  within 15 s of a stop (Dart's reconnect after `handshake_not_established`, «Повторить», a server
  switch, a resume) was therefore still "running" from the old window at its first tick.
- Effects at the new window's first tick (15 s after it started): with the old window's network
  budget spent (two restarts after a network change, the usual moment a user reconnects by hand) and
  no handshake yet, `decide()` returns `NEW_LEASE` and the brand-new window is given up
  (`watchdog_new_lease network_unrecovered`, stop reason `handshake_not_established`). With an old
  `upSince` older than 180 s and no handshake yet, it restarts the new window at once. The old
  trigger also showed «Сеть сменилась…» on a fresh connection, and a leftover `pendingCheck` of the
  old window could run inside the new one.

## Decision
- New `forgetWindowWatch()` (worker thread): `watchdog.tunnelDown()`, cancel the pending extra check
  (`pendingCheck`) and the ADR-151 state re-check. Called from `stop()` and from `prepareWindow()`
  (before every `provider.connect`, UI and restore paths).
- `beginWindow()` calls `watchdog.tunnelUp(now)`, so each window starts its own period, budget and
  backoff when its tunnel is up. A network change after that is tracked as before (ADR-146).
- No change to the watchdog's rules, timings or `restartInPlace` (an in-place restart keeps its
  window and its state, as intended).

## Alternatives
- Cancel and re-arm only the watch tick in `stop()`: rejected, the state would still leak through
  `prepareWindow` on the restore path.
- Reset in `setDeadline(0)`: rejected, `setDeadline` also runs for renewals; the reset belongs to
  the window lifecycle.

## Security and privacy
No new data, permission, log entry or network call. Fail-closed limits (deadline, Lockdown) are
unchanged; the change only stops spurious restarts and stops of a new window.

## Test plan and results
- Java substitute (javac `--release 17` + JUnit 4.13.2, not Gradle): main run **251 passed**; from
  `apps/client/android/app` **8 passed** (`RestartInPlacePatchTest` 3, `SystemIntegrationWiringTest` 4,
  new `ConnectionWiringTest` 1). New: `TunnelWatchdogTest.aNewWindowDoesNotInheritTheOldWindowsRestartBudget`
  (negative half: without the reset the new window gets `NEW_LEASE`),
  `ConnectionWiringTest.theWatchdogForgetsTheOldWindowOnStopAndOnANewWindow` (static wiring guard).
- No Dart change; `flutter test` not re-run for this commit.

## Not verified
No device run, no Gradle build. Owner phone step: switch Wi-Fi off/on until «Восстановление связи»
lasts, tap «Отключить VPN» and «Подключить» at once; the event log must not show
`watchdog_new_lease` or `watchdog_restart` within the first 15 s of the new window.

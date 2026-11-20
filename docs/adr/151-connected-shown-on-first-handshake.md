# ADR-151 — «Подключено» is shown as soon as native sees the handshake

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-144 (native state, push channel), ADR-146 (restart in place after a network change).

## Problem (tester report)
After the handshake had already succeeded and the internet worked, the app kept showing
«Восстановление связи» for about 20 s. Code reading (line numbers before this change) shows three
causes that add up to that delay:

1. **Native kept a proven restart pending.** `ConnectionState.whileUp` returns
   `recovering/network_changed` while `Facts.restartPending` is true
   (`ConnectionState.java:102`). That fact was `watchdog.pendingNetworkDelayMs(now) >= 0`
   (`ClientApplication.java:339`), which is true for as long as the watchdog's network trigger exists
   (`TunnelWatchdog.java:77-82`). After a restart in place the watchdog re-arms the trigger at the
   restart time (`TunnelWatchdog.restarted`, line 145) and only `decide()` clears it once it sees a
   newer handshake. The next `decide()` after a restart runs from `schedulePendingWatch`
   (`ClientApplication.java:1121-1124`): `lastRestart + NETWORK_GAP_MS` = **15.5 s later**. So a
   tunnel that shook hands 1 s after the restart was still reported as «Сеть сменилась,
   восстанавливается связь» for about 15 s.
2. **Nothing re-read the state between ticks.** The state is pushed only after events and on the 15 s
   watch tick (ADR-144); a handshake is not an event. Even with (1) fixed, the push would wait for
   the next tick.
3. **Dart dropped or delayed what native said.** `_onNativeState` ignored an event while a poll was
   in flight (`!_polling`, `window_connection.dart:194`), and a poll rendered native's state only at
   its end (`window_connection.dart:1487`), after one or two API round trips through the fresh
   tunnel (5 s connect timeout each).

The Android 2 s poll plus (1) plus the 0.5 s settle give the observed ~17-20 s.

## Decision
1. `TunnelWatchdog.awaitingNetworkProof(now, handshakeAgeMs)`: true only while the trigger is pending
   **and** no handshake newer than it is known (the same "newer than the change" rule as `decide()`).
   It is read-only; `decide()` still owns the trigger and the restart / new-lease budget.
   `ConnectionState.Facts.restartPending` and the diagnostic `transport` value use it.
2. Native fast re-check: after `beginWindow()`, a restart in place and a network path change (with a
   tunnel up), native re-reads and publishes the state every 1 s (`ConnectionState.RECHECK_MS`) for at
   most 30 s (`RECHECK_FOR_MS`), and stops as soon as the state is `connected` or the tunnel is down
   (`ConnectionState.recheckSoon`, pure). One pending re-check at a time. Display only: it restarts,
   stops or escalates nothing. `publishState(boolean)` now returns the state it computed.
3. Dart: an event for the connection this controller holds is rendered also while a poll runs, and a
   poll renders native's state from its first snapshot before the API call (the end of the poll still
   renders the final snapshot).

## Alternatives
- Clear the watchdog trigger from `connectionState()`: rejected, it would mix the display with the
  restart budget (ADR-146 4.4).
- Shorten the 15 s restart gap: rejected, it changes restart behaviour, not the display.
- Poll the handshake at 1 s all the time: rejected, needless work and battery while connected.

## Assumptions
- The handshake time is whole wall seconds, so "newer than the change" may be off by up to 1 s (the
  same imprecision `decide()` already has). At worst a handshake from up to 1 s before a restart is
  taken as after it; the watchdog's own decision is unchanged by this ADR.
- `lastHandshakeSeconds()` once a second for at most 30 s after such a trigger is cheap enough.

## Security and privacy
No new permission, data, log entry, network call or channel field. The pushed event carries the same
codes. Routing, Lockdown, deadlines and the restart / new-lease rules are unchanged.

## Test plan and results
- Java substitute (javac `--release 17` + JUnit 4.13.2, not Gradle): main run **249 passed**, plus
  **7 passed** from `apps/client/android/app` (`RestartInPlacePatchTest`, `SystemIntegrationWiringTest`).
  New: `TunnelWatchdogTest.aHandshakeAfterTheRestartEndsTheProofWaitBeforeTheNextCheck` (incl. the
  negative case: a handshake from before the restart proves nothing),
  `ConnectionStateTest.theFastRecheckStopsWhenConnectedDownOrAfterThirtySeconds`,
  `ConnectionStateTest.aProvenRestartShowsConnectedWhileTheTriggerIsStillPending`.
- `flutter test` (apps/client): **318 passed** (316 before). New: a poll shows native's state before
  its API round trip ends; a pushed event is rendered while a poll is in flight (negative: an event
  for another connection is not rendered).
- `flutter analyze` on an ASCII copy: only the expected `assets/orbit/` warning. `dart format`: clean.
- The `ClientApplication` re-check wiring has no JVM test (compiled only).

## Not verified
No device run, no Gradle build. Owner phone step: connect, switch Wi-Fi off/on; the title should be
«Подключено» within about 2 s of the new handshake (event log: `watchdog_restart`, then
`state connected_verified` within seconds, not 15 s).

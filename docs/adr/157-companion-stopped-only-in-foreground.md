# ADR-157 — The recovery companion is stopped only once it is in the foreground

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-147 (3.3, `RecoveryCompanionService`).

## Problem (Stage 2 audit)
`ClientApplication.updateDozeAids()` starts the companion with `startForegroundService()` when the
VPN is wanted and no tunnel is up, and calls `stopService()` as soon as that is no longer true
(`!wanted && (companionRequested || companionRunning)`). The companion calls `startForeground()`
in its `onStartCommand`, on the main thread, later.

`updateDozeAids()` runs after every recovery decision, window start and user disconnect, on the
worker thread. When a tunnel comes up (or the user disconnects) shortly after a start request —
for example a Dart reconnect that finishes a second after a network callback opened the gap, while
the main thread is busy — `stopService()` reaches a service that has not called
`startForeground()` yet. Android's contract for `startForegroundService()` is that the service must
call `startForeground()`; AOSP (`ActiveServices`, since Android 9) reports a service brought down
in that state as a crash of the app ("Context.startForegroundService() did not then call
Service.startForeground()"). The VPN service runs in the same process, so such a crash also drops
the tunnel — a "random drop" with no connection-level cause in the event log.

## Decision
- In the stop branch, `companionRequested` is still cleared, but `stopService()` is called only when
  the companion is running in the foreground (`DozeSurvival.companionMayStop(companionRunning)`).
  Otherwise the stop is deferred (event `companion_stop_deferred`).
- `companionStarted()` (main thread, after `startForeground()`) already re-runs `updateDozeAids()`
  on the worker; it now finds the companion running and not wanted, and stops it. If the companion
  is wanted again by then, it simply stays.
- A refused start (`companionRefused`) is unchanged.

## Alternatives
- Stop it from `onStartCommand` with a "stop pending" flag: equivalent, but splits the decision
  between two classes; the existing re-check in `companionStarted()` already does it.

## Assumptions
- The platform crash for a service stopped before `startForeground()` applies to the target SDK 36
  devices (AOSP behaviour since API 28). Not reproduced on a device here.

## Security and privacy
No new data, permission or notification. At worst the low-importance «DetourVPN восстанавливает
подключение» notification stays a moment longer, until the companion reaches the foreground and is
stopped.

## Test plan and results
- Java substitute (javac `--release 17` + JUnit 4.13.2, not Gradle): main run **254 passed**; from
  `apps/client/android/app` **10 passed**. New: `DozeSurvivalTest.theCompanionIsStoppedOnlyOnceItRunsInTheForeground`,
  `ConnectionWiringTest.theCompanionIsNeverStoppedBeforeItIsInTheForeground` (static guard: the only
  `stopService` is after the running check; `companionStarted` sets the flag before re-checking;
  the service reports after `startForeground`).
- No Dart change.

## Not verified
No device run. A device check would need a forced race (slow main thread); the event log shows
`companion_stop_deferred` when the deferral happens.

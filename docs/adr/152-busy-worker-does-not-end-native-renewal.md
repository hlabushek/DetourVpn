# ADR-152 — A busy worker no longer ends native renewal

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-097 (native renewal), ADR-118 (burst attempts), ADR-140/147 (renewal timing).

## Problem (tester report: rare random drops)
Testers saw the tunnel drop now and then without a visible reason. Code reading found a race that
ends a working window without any network or server cause:

- `NativeRenewer.attempt` reads the window through `ClientApplication.renewalView`, which runs on the
  single tunnel worker and waits at most 5 s (`worker.submit(...).get(5, SECONDS)`,
  `ClientApplication.java`, `renewalView`). **Any** failure of that read, including a
  `TimeoutException`, was treated as "the tunnel stopped": `finish(s, "tunnel_stopped")`
  (`NativeRenewer.java:168-171` before this change). `finish` calls `stop()`, so native renewal
  ended for good for this window.
- The worker is a single thread (`ClientApplication.java:30`) shared by every channel call. One of
  them, `apps` (`ClientActivity.installedLauncherApps`), draws a 96x96 bitmap and PNG-encodes it for
  **every** launcher app; on a phone with many apps this takes seconds. Other calls queue behind it
  (connect, `restartInPlace`), so a renewal check (every 30 s while awake, `AWAKE_CHECK_MS`, and at
  each alarm) that lands in such a burst times out.
- With native renewal ended, nothing renews the window while the app is in the background (the UI
  pauses its polling while native renews, ADR-144), so the window runs out and the native deadline
  stops the tunnel (`lease_expired`). The diagnostic shows `native_renewal_stopped tunnel_stopped`
  although the tunnel was running.

## Decision
1. `NativeRenewal.viewBusy(Throwable)` (pure): only a `TimeoutException` means "busy". On busy the
   renewer logs `native_renewal_wait worker_busy` and looks again after
   `NativeRenewal.WORKER_BUSY_RETRY_MS` (5 s, in-process timer and alarm); it does not stop.
   Every other failure (tunnel down, a new generation after a disconnect) still ends the renewal as
   before. The retry is bounded by the window: once the deadline passes, the view reports no time
   left and the renewal ends with `lease_expired`, as before.
2. `renewalView` cancels its queued read on timeout, so a stale read never runs late.
3. The `apps` channel call runs on its own single-thread executor in `ClientActivity` instead of the
   tunnel worker. It reads only the package manager (no tunnel state), so nothing it needs is lost,
   and it no longer depends on the tunnel provider being initialised.

## Alternatives
- A longer timeout: rejected, any bound can be exceeded and the "stopped" reading would still be
  wrong.
- Moving the renewal view off the worker: rejected, the deadline and provider state are owned by the
  worker (thread confinement, ADR-126).

## Assumptions
- A `TimeoutException` from the 5 s bound is the only "busy" signal; the read itself (deadline check,
  `isUp`, traffic counters) is short when the worker is free.
- `advanceNative` keeps its existing behaviour on a timeout (a transient failure, retried with the same
  idempotency key; `WindowDeadline.advance` is idempotent for an equal generation).

## Security and privacy
No new permission, data, network call or exported component. The renewal token, origin and the
fail-closed native deadline are unchanged; a busy worker can delay a renewal attempt but can never
extend a window. The new log entry is a fixed code.

## Test plan and results
- Java substitute (javac `--release 17` + JUnit 4.13.2, not Gradle): main run **250 passed**, plus
  **7 passed** from `apps/client/android/app`. New:
  `NativeRenewalTest.aTimedOutRenewalViewIsBusyAndEveryOtherFailureStopsTheRenewal` (negative cases:
  `IllegalStateException`, `ExecutionException`, `InterruptedException`, null are not busy).
- `NativeRenewer` and `ClientActivity` wiring: compiled only (no JVM test; they need Android).
- No Dart change; `flutter test` not re-run for this commit.

## Not verified
No device run, no Gradle build. Owner phone step: connect, put the app in the background, open the
split-apps screen a few times around a renewal; the event log must not show
`native_renewal_stopped tunnel_stopped` while the tunnel is up, and the window keeps renewing.

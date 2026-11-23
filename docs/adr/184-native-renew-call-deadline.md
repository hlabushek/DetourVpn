# ADR-184 — Overall deadline for a native renewal HTTP call (ADR-165 item 2)

**Status:** accepted. Code in the working tree, uncommitted. **Not built into an APK, not run on a phone.**

## Context
`NativeRenewer.call` uses `HttpURLConnection` with a 10 s connect and a 10 s read timeout. Those
apply per step, not per call. A server that answers slowly, one byte at a time, can keep the
single renewer thread busy past the window's end, so renewal stops being retried in time.
ADR-165 item 2 asked for an overall per-call deadline.

## Decision
A watchdog on a dedicated daemon thread (`WATCHDOG`) disconnects the `HttpURLConnection` after
`CALL_DEADLINE_MS` (25 s: connect, write and read each up to 10 s, plus margin). The call then
fails with an IOException, which `attempt()` already treats as transient and retries inside the
window. The watchdog is cancelled in `finally`, so a normal call leaves nothing scheduled.

The watchdog does not use the renewer's own `io` thread: that thread is the one blocked in the
call, so a task queued on it would never run in time.

## Not covered
- DNS resolution. `disconnect()` cannot interrupt a lookup that is already in progress. A stuck
  lookup still holds the renewer thread. Bounding it needs a resolve on another thread or a
  pre-resolved address, which is a separate change.
- The `ProtectedHttps` path (Lockdown) has its own timeout argument and is not changed here.

## Verification
- `javac` with the Android Studio JBR (Java 21) against `android-36/android.jar`: no diagnostics
  in `NativeRenewer.java` (only the existing `URL(String)` deprecation warning). Other files in
  the package fail to resolve because their dependencies (generated plugin registrant, libbox,
  `SecureVault` types) are not on the classpath; this compile is therefore not a full build.
- Not run: Gradle build, unit tests, a device or Lockdown scenario.

## Related
- [[165-paranoid-audit-open-items]] — item 2 is partly closed by this ADR (DNS remains).
- ADR-152, ADR-118: the retry and burst behaviour that consumes the `IOException`.

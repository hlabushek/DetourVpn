# ADR-164 — A failed native stop inside a window cleanup is handled, not thrown

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Unit-test
verified only; not verified on a device.** Found by the paranoid-mode audit.

## Defect (proven in a unit test)
`WindowConnectionController._cleanup` called `await native.disconnect()` without a catch. It runs
from the catch blocks of `_refresh` (every poll timer tick) and `_connect` (every connect tap and
automatic reconnect). When the native stop failed — for example with the engine unavailable after a
failed cold-start init (ADR-155: every channel call except storage throws while `provider` is null)
— the exception escaped:
- from the poll: `Timer.periodic(... unawaited(refresh()))` → an unhandled asynchronous error on
  every tick;
- from `connect()`: an unhandled error at the button handler;
- and the rest of the failure handling was skipped (no reconnect scheduling, no end of the user's
  intent on a terminal failure, no `remotePending`).

Reproduced: a running window, then `native.up = false` and a throwing `disconnect()` →
`refresh()` threw `Bad state`. The legacy `ConnectionController._performCleanup` already handles the
same case (`native_disconnect_failed`, keep the lease, no revoke).

## Decision
`_cleanup` catches a failed stop the same way as the legacy controller: `error =
'native_disconnect_failed'`, `diagnosticStage = 'native_disconnect'`, phase failed, return without
revoking (a revoke under a tunnel that may still run is avoided; the native deadline and Lockdown
remain the fail-closed boundary). The lease is kept, so the next poll sees the stopped tunnel again
and retries the stop and the revoke.

## Security and privacy
No weakening: nothing is revoked or forgotten on an unconfirmed stop; the native deadline still ends
the tunnel. No new logging.

## Test plan and results
`apps/client/test/paranoid_audit_test.dart`: "never escapes a poll and revokes nothing" (and the
next poll finishes the cleanup), "never escapes a failed connect". Both failed before the change.
Full `flutter test` run in HANDOVER.

## Not verified
No device run.

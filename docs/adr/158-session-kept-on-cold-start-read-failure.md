# ADR-158 — A failing session read or a non-401 refresh answer keeps the saved session

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Unit-test
verified only; not verified on a device.**
**Builds on:** ADR-100 (sign-in survives no network at startup), ADR-155 (native init retried).

## Problem
At a cold start `_VpnAppState.restore()` (`main.dart`) called `Api.restoreSession()`, which reads
the encrypted session record through the platform channel (`sessionRead`, Android keystore). Any
exception that was not one of three transient `ClientError` codes landed in a catch-all that called
`Api.forgetSession()`, which writes `null` over the stored record. So:

- a keystore error at the first read of the process (a `PlatformException`, the same class of
  failure ADR-155 retries for the native init) signed the user out and destroyed the refresh token;
- a refresh answered with a 5xx that carries an error code (for example `auth_unavailable`, 503)
  was not in the transient set, so `Api._renew` erased the session as if it were a refusal — this
  contradicts the ADR-100 rule "only an explicit refusal (401) or a storage failure ends it";
- `Api.close()` (widget dispose) during a restore bumps the session epoch, `_renew` reports
  `session_expired`, and the old catch-all then erased a session that nobody had refused.

## Decision
- `Api.restoreSession()` maps a failing `store.read()` to `ClientError('session_storage_unavailable')`
  and does not touch the stored record.
- `Api._renew()` erases the session only when the refresh request is answered with HTTP 401
  (`unauthorized`, the server's only refusal status for `/auth/refresh`). Every other error is
  rethrown with the durable token kept. Storage failure after a successful rotation is unchanged
  (still ends the session, ADR-100).
- `restore()` treats only `session_expired` as final (Api raises it after it already erased the
  record, or after another actor changed the session). Every other failure, including non-`ClientError`
  exceptions, goes through the existing bounded retry (1, 2, 4, 8, 15 s) and then keeps the session
  for the next resume or launch (`_sessionKept`). The catch-all no longer calls `forgetSession()`.
- "Confirmed no session" stays as before: nothing stored → login; a record for another origin or
  without a refresh token → erased inside `restoreSession()`.

## Assumptions
- The server answers a refused refresh only with 401 (`AuthError` default status;
  `services/api/src/vpn_api/auth/service.py` `_refresh`). A malformed stored token that the server
  rejects with 422 now keeps the session and the user sees the login screen after the bounded retry;
  a new login overwrites it. This is preferred over erasing on an unknown status.
- While a read error persists the user sees the login screen after about 30 s (the existing
  recoverable state); the next resume retries. A new sign-in simply replaces the record.

## Security and privacy
No token is extended or fabricated. A refused session (401) is still erased at once. Keeping a
record on a read error only keeps what was already stored in the app-private, keystore-encrypted
store. No new logging; error codes only.

## Test plan and results
- `apps/client/test/api_test.dart`: "a failing session read keeps the stored session and sends
  nothing", "a refresh answered with a non-401 error keeps the session"; the existing "an explicit
  refresh refusal erases the session" still passes.
- `apps/client/test/widget_test.dart`: "a cold-start session read failure never erases the session"
  (six reads over the retry schedule, zero writes, login screen shown).
- `flutter test test/api_test.dart test/widget_test.dart`: 23 passed. Full run recorded in HANDOVER.

## Not verified
No device run; a real keystore failure cannot be reproduced in a unit test.

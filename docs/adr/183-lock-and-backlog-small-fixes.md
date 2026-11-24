# ADR-183 — Three small lock and backlog fixes (audit L3, L4, L5)

**Status:** accepted. Code and tests committed. **Not deployed.**

## L3 — first use is marked before the request takes locks
`authenticate()` called `_mark_used` (its own short transaction on a second pooled connection)
after it had locked the user and session-family rows. That second connection could wait while
the request held those locks, which the database cannot see as a deadlock, and under load it
could exhaust the pool. `_mark_used` now runs right after the credential row is found, before
any lock. "Used" now means "presented": a holder that presents a credential is marked even when
the request is then refused (expired, revoked, disabled account). This only makes the ADR-100
refresh-replay grace stricter (a successor that was presented is not a lost response).
Still two pooled connections on a first use, but never while this request holds row locks.

## L4 — a renew token is checked before the owner row is locked
`token_connection` locked the owner's user row before checking the token, so anyone who knew a
connection id could queue requests on that user's lock. The token (and the connection state) is
now checked on an unlocked read first; an invalid token returns 401 with no lock taken. A valid
token then takes the ADR-167 order (owner, then connection) and is checked again under the
locks, because it may have been rotated or revoked meanwhile. Constant-time comparison is kept
on every path.

## L5 — a receipt backlog defers a grant instead of closing it
`WindowRuntime.apply` refuses a new grant on a slot whose earlier receipt is not yet
acknowledged (`receipt_ack_required`). The daemon treated that like any refusal and closed the
connection. The daemon now reports `window_command_deferred` and leaves the grant alone (nothing
was changed by `apply`); the server keeps it pending and the next poll, after the
acknowledgement, applies it. If the grant becomes too late meanwhile, the normal late-grant
path closes it (ADR-179: `not_opened`).

## Tests
- `services/api/tests/test_lock_and_backlog_small_fixes.py`: (PostgreSQL) the first-use update
  is executed before the user row lock; (PostgreSQL) with the owner row locked elsewhere, a wrong
  renew token is refused within a 500 ms lock timeout while a valid one waits for the lock, and
  succeeds once it is free; (SQLite journal) a backlog defers the grant without fencing and the
  grant is applied after the acknowledgement.
- Related: `test_authentication.py`, `test_window_service.py`, `test_window_daemon.py`,
  `test_audit_stage2_daemon.py`, `test_audit_stage2_lock_order.py`.

## Not verified
- No load test of the pool; no real node; no managed database.

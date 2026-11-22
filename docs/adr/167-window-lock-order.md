# ADR-167 — Lock order for window connections (deadlock fix)

**Status:** accepted. Code and unit/PostgreSQL tests committed locally. **Not deployed.**

## Context
On the beta API (managed PostgreSQL, 2026-11-22) the Germany and Poland node agents lost their
control connection every few minutes (`control_unavailable` / `control_recovered`). The Neon
dashboard showed deadlocks in the same window (about 6 in one burst). The agent path is the
node poll (`poll_windows`), which is authenticated by the node.

`poll_windows` locks every owning user row in UUID order first, then the connection rows
(`session.refresh(candidate, with_for_update=True)`). Owner-facing paths did the reverse:
`owned_connection` (renew, revoke, get) and `token_connection` (native renew) locked the
`peer_connections` row first, and only later locked the user row inside window funding
(`reserve_window` -> `LedgerService._lock_owner`). Two transactions in opposite order deadlock.

## Decision
1. `owned_connection` takes the user row lock (`LedgerService._lock_owner`, `active=False`)
   before the connection row lock. The user id comes from the authenticated principal.
2. `token_connection` reads the connection's owner without a lock, takes the owner lock, then
   takes the connection row lock and runs the existing checks. The owner of a connection never
   changes, so the unlocked read is safe.
3. The global order is: user rows (sorted) -> connection rows -> node advisory lock -> node row.
   `start_epoch` takes the node advisory lock without user locks, so it cannot form a cycle.

## Consequences
- Renewals and revokes wait a little longer behind a node poll that holds the same user.
- Deadlocks should disappear from this path. This is not yet proven on the managed database.

## Tests
- `test_window_service.py`, `test_peer_windows.py`, `test_window_funding.py`,
  `test_window_runtime.py`, `test_i032_windows_zip_receipt.py` on a temporary local PostgreSQL:
  114 passed, 2 errors. The 2 errors are the environment: the portable PostgreSQL has no
  `pg_dump` (`postgres_tool_missing`), so the backup fixtures cannot run.
- No dedicated concurrency test yet (two transactions in opposite order). Add one before relying
  on this fix for production.

## Not verified
- The live deadlock text from Neon logs (only the dashboard counter was seen).
- Behaviour after deploying to the primary and backup APIs.

## Follow-ups
- Capture the deadlock detail from PostgreSQL logs during the next event to confirm the pair of
  statements.
- Add a PostgreSQL concurrency test for owner-vs-poll lock order.

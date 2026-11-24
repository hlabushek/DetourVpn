# ADR-173 — One connection's bad data does not stop the node's poll

**Status:** accepted. Code and PostgreSQL tests committed. **Not deployed.**

## Context
`poll_windows` handled the whole node in one transaction. One unknown or foreign receipt
(`NodeError` 404), one receipt the contract rejects (`ContractError`), one settlement failure
(`QuotaError("quota_inconsistent")`) or one connection whose hold no longer matches
(`QuotaError("window_inconsistent")`) failed the whole poll. The node resent the same receipt
every second, so every user on that node lost renewals and admissions. Found by the
2026-11-22 audit (M2).

## Decision
1. **Receipts.** An unknown or foreign receipt is skipped (never acknowledged). Each known
   receipt is settled inside a savepoint (`session.begin_nested()`); `QuotaError`,
   `NodeError`, `ValueError` (including `ContractError` and pydantic errors) and
   `IntegrityError` roll back only that savepoint, and the receipt is not acknowledged. The
   node keeps it and resends it; the other receipts are settled.
2. **Candidates.** `current_grant` and `grant_authorized` only read, so no savepoint is
   needed: the same errors skip that one connection (no command, state unchanged).
3. **Recovery commands.** A hold whose stored grant no longer validates is skipped.
4. **Log.** One fixed line per skipped item, logger `vpn_api.windows`, level WARNING:
   `window_poll_item_failed code=<fixed code>`. No user, connection or window identifiers.
   Codes are the error's own `[a-z_]` code, else `window_item_invalid`.
5. Database errors (`OperationalError`, deadlocks, timeouts) still fail the poll: they are not
   about one item.

## Consequences
- A poisoned receipt now blocks only its own slot on the node (`receipt_ack_required` on that
  slot), not the node. An operator still has to resolve it; the log shows that it happens.
- A cross-node receipt used to fail the whole poll; it is now refused alone. It is still never
  settled or acknowledged.
- One savepoint per receipt adds two statements per receipt (receipts are few per poll).

## Tests
- `services/api/tests/test_window_poll_isolation.py` (PostgreSQL): a corrupted connection and a
  receipt over budget are skipped with their codes logged, while a third user's receipt is
  settled in the same poll; nothing of the bad receipt is written; a foreign receipt is refused
  without failing the poll.
- `test_window_service.py::test_cross_owner_and_cross_node_receipts_fail_closed` updated to the
  new behaviour (refused, not acknowledged, poll succeeds).

## Not verified
- No real node, no managed database.

# ADR-168 — A node poll answers at most 32 commands

**Status:** accepted. Code and PostgreSQL tests in the working tree (not committed: commits
were blocked by the permission system on 2026-11-23). **Not deployed.**

## Context
`WindowCommands.commands` has `max_length=32` (`v3_contracts.py`). `poll_windows` built one
command per due connection (pending → `window`, revoke_pending → `revoke`) with no cap. A pool
node holds up to 250 peers, so 33 or more due connections at once (a burst of renewals after an
API outage, a mass revoke) made building the response raise a pydantic `ValidationError`. The
route answered 500 and rolled back the whole transaction, including the node's `last_seen_at`.
Revokes only clear through node receipts, which need a successful poll, so the node stayed
stuck: no admissions after 10 s, no renewals after 120 s. Found by the 2026-11-22 audit (C2).

## Decision
1. `poll_windows` still evaluates every candidate and applies every state change (revoke
   decisions, `active` on a reported window), exactly as before.
2. The command list is then sorted (revokes first, then by the grant's `not_after`,
   nearest first) and cut to `MAX_POLL_COMMANDS = 32`.
3. Commands left out are not lost: their connections stay `pending` or `revoke_pending` and are
   due again on the next poll (the agent polls every second).
4. The contract does not change. The recovery branch was already limited to 16.

## Consequences
- Revokes drain first; they free slots and stop traffic that is no longer funded.
- With more than 32 due windows, the last ones are dispatched one or more seconds later. A
  renewal near its deadline is ordered before one with more time left.
- If more than 32 revokes are due for a long time, windows wait behind them. This needs a node
  that keeps failing to close grants, which is a separate failure.

## Tests
- `services/api/tests/test_audit_stage2_poll_commands.py` (PostgreSQL): a 33-peer pool node
  with 33 pending connections answers 32 commands in deadline order and the 33rd in the next
  poll; a revoke is sent first while 32 windows are pending.
- Related: `test_window_service.py`, `test_peer_windows.py`, `test_window_funding.py`.

## Not verified
- No deploy; no real node with more than 32 due connections.

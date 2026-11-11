# ADR-120 — One explicit renewal margin; no grace after a window ends

**Status:** proposed 2026-11-11 (connection reliability, `RELIABILITY_ROOT_CAUSE.md` §1).

## Context
The server refuses to renew, and revokes on the next node poll, a window whose `not_after` is
within 5 s (`grant_authorized`, literal `timedelta(seconds=5)`). The node contract already has
`RENEWAL_MARGIN_SECONDS = 5` in `v3_contracts.py` and refuses a renewal grant with
`renewal_too_late` inside the same margin. The node gate itself closes 6 s before `not_after`.
The two values are the same today but live in two places.

## Decision
- `grant_authorized` uses `RENEWAL_MARGIN_SECONDS` from `v3_contracts`. Behaviour is unchanged.
- No grace period after `not_after`. At that point the node has already closed the gate and
  removed the peer (`WindowRuntime.expire`), so a late renewal would reopen access that was not
  funded and would need a node contract change. A late client reconnects (ADR-099/119).
- Tests pin the boundary: a renewal with more than the margin left is accepted; one inside the
  margin is refused with `window_not_renewable`.

## Limits
- Server unit tests only; no deployment.

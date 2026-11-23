# ADR-178 — One clock for admission and node liveness; node clocks are checked at install

**Status:** accepted. Code and tests in the working tree, uncommitted. **Not run against any host.**

## Context
- **M8.** `verify_and_consume` set `node.last_seen_at` from the API host's clock, while
  `available_runtime` compared it with the database clock (10 s and 120 s thresholds). Skew
  between the two changed whether a node was admitted.
- **M7.** A node more than 1 s behind the database clock refuses every grant (the grant allows
  1 s of server-ahead skew). Neither the candidate check nor the install checked NTP or the
  clock, and each refused grant holds its budget (see H4).

## Decision
1. `last_seen_at` is written from the database clock (`clock_timestamp()`), the same clock
   admission compares with. The agent's request timestamp is still checked against the API
   host's clock (±60 s, unchanged): that check is about the agent's clock, not liveness.
2. `scripts/check_candidate_node.py` (run before every install) adds two required checks:
   - `ntp_synchronized`: `timedatectl show -p NTPSynchronized` is `yes`;
   - `clock_within_1s`: the node's epoch in milliseconds, read inside the SSH session, minus
     the workstation's clock at the midpoint of the SSH call, is at most 1000 ms in absolute
     value. The error of that midpoint is at most half the round trip.
3. Nothing else changes. A node that fails either check is not installed.

## Owner decision needed
- The workstation's clock is the reference for the install check. The owner keeps the
  workstation on NTP. A node that is correct but a long way from the workstation (a slow or
  asymmetric link) can fail `clock_within_1s`; the check then refuses and reports, and the owner
  decides whether to widen it. Widening it past 1 s is not safe without also widening the grant
  skew allowance.
- The node-side preflight (`services/node-agent/beta_node_preflight.py`, run on every service
  start) does not check NTP. Adding that check would block restarts on a node whose time source
  is briefly unsynchronised; the owner decides.

## Tests
- `services/api/tests/test_audit_stage4_clock.py` (PostgreSQL): `last_seen_at` equals the
  database clock even when the API host's clock is 30 s ahead.
  - Candidate checks: NTP `no` or `unknown` is refused; an offset of 1001 ms, -1001 ms or an
    unreadable offset is refused; 1000 ms and -1000 ms pass.
  - The offset is measured around the SSH call (fixed clock values).
- `services/api/tests/test_beta_nodes.py`: the shared "ready host" fixture now carries NTP and
  offset values, because the two checks are required.

## Not verified
- No node, no real clock skew, no real `timedatectl` output.

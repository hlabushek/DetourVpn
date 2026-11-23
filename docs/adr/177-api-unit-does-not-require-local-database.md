# ADR-177 — The beta API unit does not hard-require the local database unit

**Status:** accepted. Unit text and test in the working tree, uncommitted. **Not installed on
any host.**

## Context
`infra/beta/detourvpn-beta-api.service` had `Requires=detourvpn-beta-database.service`. With
the API on the managed database (ADR-166), the local database container is not needed by the
API. A failed or stopped local database unit stops the API too, and `Restart=on-failure` cannot
start it while the dependency is down. Found by the 2026-11-22 audit (M4).

## Decision
`Requires=` becomes `Wants=` for the database unit; `After=` is kept, so ordering is unchanged
when both start. The restart policy is unchanged.

## Owner decision needed
- Whether a host still running the API on the local database should keep a hard dependency. With
  this change, starting the API before its local database fails at the first query instead of
  never starting. The owner confirms the primary's database choice before installing this unit.
- The restore drop-in (`scripts/restore_primary_api.sh`) writes `Restart=always` and
  `StartLimitIntervalSec=0` into its own file; it still overrides this unit on the host.

## Tests
- `infra/ansible/tests/test_beta_api_unit_dependencies.py`: no `Requires=`/`BindsTo=` on the
  database unit, `Wants=` and `After=` present, restart policy unchanged.

## Not verified
- No `systemd-analyze verify` on a host; the unit was checked as text only.

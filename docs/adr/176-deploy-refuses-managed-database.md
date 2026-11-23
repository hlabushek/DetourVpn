# ADR-176 — The beta deploy script refuses a primary that reads the managed database

**Status:** accepted. Code and tests in the working tree, uncommitted. **Not run against any host.**

## Context
`scripts/deploy_beta_api_latest.py` takes its schema backup (`pg_dump` by `docker exec`), its
rollback check (`q()`) and its migration (`scripts/closed_migrate.py`, through a URL it writes to
`127.0.0.1:25433`) from the local Docker PostgreSQL. ADR-166 moved the primary's API to the
managed database. Run against such a primary, the script would dump and migrate the wrong
database, then replace the code, and its rollback would restore the code while the managed schema
stayed migrated. Found by the 2026-11-22 audit (M3).

## Decision
Refuse. The remote preflight checks that `/etc/detourvpn-beta/api.env` holds a
`VPN_DATABASE_URL` for `127.0.0.1:25433/detourvpn_beta`. Otherwise it prints
`refused=database_not_local_docker` and exits before anything changes (before `changed=1`, before
the service stops, before the dump). The script is not extended to manage the managed database,
because its rollback could not undo a managed schema migration.

## Owner decision needed
- A deploy to the managed-database primary needs its own runbook: a schema backup on the managed
  side (Neon branch or dump with the direct endpoint), a migration with a tested downgrade, and
  its own rollback. Until then the primary's API code is deployed by hand or by a new script
  reviewed for that case. The owner decides whether this is needed before the beta.

## Tests
- `services/api/tests/test_deploy_beta_guard.py`: the guard accepts the local Docker URL (quoted
  and unquoted) and refuses the managed URL, another port and another host; the refusal comes
  before the first host change and before `pg_dump`; the generated remote program passes
  `bash -n`.

## Not verified
- No run against a host or a database. The guard regex matches the URL format written by
  `scripts/deploy_beta_api_latest.py` and `infra/beta` (password letters and digits only, as the
  restore script states). A password with other characters would be refused, which is the safe
  side.

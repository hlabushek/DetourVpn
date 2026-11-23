# ADR-180 — Backups and maintenance may target the managed database (verify-full only)

**Status:** accepted. Code, unit files (repo only) and tests in the working tree, uncommitted.
**Not deployed; no backup was taken from the managed database.**

## Context
`operations.local_url` accepted only 127.0.0.1/::1, so the beta backup, restore drill, monitor
and the maintenance timer could not run against the managed database. The backup and
maintenance units also `Requires=` the local database unit. After the move to the managed
database there was no backup of the live data, and nonce/rate/auth cleanup and ledger
reconciliation did not run on it. Found by the 2026-11-22 audit (H6).

## Decision
1. `local_url` keeps the loopback rules unchanged. A non-loopback host is accepted only when the
   URL carries an explicit `sslmode=verify-full` and passes the API's own parser
   (`vpn_api.db.url.parse_database_url`, ADR-166): a DNS name with a dot, no IP literal, no
   `localhost`, only `sslmode` and `channel_binding=require` as query keys. Every refusal keeps
   the historic code `local_database_required`. The returned URL always carries `sslmode`
   (and `channel_binding` when given).
2. `engine_source(engine)`: the API engine (`create_database_engine`) strips the query of a
   remote URL and enforces verify-full through connection arguments; this helper writes
   `sslmode=verify-full` back into the URL before the check, so backups from that engine pass.
3. `local_engine` passes `sslrootcert` (the distribution CA bundle, as the API does) for remote
   hosts. `pg_environment` sets `PGSSLMODE=verify-full`, `PGSSLROOTCERT` and, when given,
   `PGCHANNELBINDING` for `pg_dump`/`pg_restore`.
4. `detourvpn-beta-backup.service` and `detourvpn-beta-maintenance.service` use
   `Wants=` instead of `Requires=` for the local database unit (`After=` kept), like ADR-177.

## Owner decision needed
- Point `/etc/detourvpn-beta/operations.env` at the managed database (direct endpoint,
  `sslmode=verify-full`) — or keep it on the local database. Until it is changed, backups
  still copy the local database, which no longer holds the live data.
- The host's `/usr/bin/pg_dump` must be at least the managed server's major version.
- The restore drill creates a scratch database (`CREATE DATABASE`); the managed role may not
  have that right. Running the drill against a separate test branch is safer.

## Tests
- `services/api/tests/test_operations_remote_database.py`: remote URLs without explicit
  `verify-full` (absent, `require`, `verify-ca`, `disable`, `prefer`) and with extra libpq keys
  or weak channel binding are refused; IP literal, dotless host and `localhost` are refused; a
  `verify-full` URL is accepted with its TLS settings in the child environment; loopback rules
  unchanged; an API engine URL passes through `engine_source`; both units no longer require
  the local database unit.
- Related: `test_operations.py`, `test_beta_backup.py`, `test_maintenance.py`,
  `infra/ansible/tests/test_beta_config.py`.

## Not verified
- No backup, restore or maintenance run against a managed database.

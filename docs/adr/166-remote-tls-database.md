# ADR-166 — Remote TLS database for the API (managed PostgreSQL, e.g. Neon)

**Status:** accepted for the beta HA plan. Code and unit tests committed locally; **not deployed,
not connected to a real remote database, not verified against Neon.** This supersedes the
"loopback only" rule of the database layer for remote hosts only. Loopback behaviour is unchanged.

## Context
The API was restricted to a loopback PostgreSQL with TLS disabled (`db/url.py`, `db/connection.py`,
the I-004 development rule). That blocks the HA plan (ADR-163): a backup API must read the same data
as the primary, so both must use the same managed database (decided by the owner, 2026-11-22).
Neon is a remote host and requires TLS, so the old rule rejects it at startup.

## Decision
1. **Remote hosts are accepted** when they are a DNS hostname with at least one dot. IP literals,
   `localhost`-like names and anything without a dot are refused for remote targets.
2. **TLS is mandatory for remote hosts.** `sslmode` must be `require`, `verify-ca` or `verify-full`.
   The default when `sslmode` is absent is `verify-full`. `disable`, `allow` and `prefer` are refused.
3. **Query parameters are an allowlist** for remote targets: only `sslmode` and `channel_binding`
   (value `require`). Everything else is refused (`hostaddr`, `service`, `sslrootcert`,
   `options`, `passfile`, ...), because libpq would honour them and redirect the connection.
4. **Loopback is unchanged**: loopback host, no query parameters, `sslmode=disable`, `hostaddr` pinned
   to loopback (I-004 development path, the local operations scripts, and the existing beta tooling).
5. **CA bundle:** remote connections use `sslrootcert=system` (libpq 16+ system trust store), so
   publicly trusted managed-provider certificates verify without a bundled CA file.
6. The username/password rules for closed operation are unchanged: the closed validator still
   requires the dedicated `vpn_api` role with a password.
7. Still refused everywhere: non-`postgresql+psycopg` drivers, missing user or database, database
   names outside `[A-Za-z_][A-Za-z0-9_]{0,62}`, invalid ports, and error messages that echo the URL.

## Security notes
- `sslmode=require` encrypts the link but does **not** verify the server certificate, so a
  network attacker could impersonate the database. It is accepted because the owner asked for it,
  but **use `sslmode=verify-full` in production**; the README gives the exact string.
- The database password lives in the API environment file (mode 0640). It is never logged; the
  settings repr and validation errors are masked (tests cover this).
- Neon's **pooled** endpoint (host containing `-pooler`) runs PgBouncer in transaction mode and can
  break server-side prepared statements that psycopg uses. Use the **direct** (non-pooled) endpoint
  for the API and for migrations unless a later ADR tests the pooler.
- The database is no longer private to the host. Network reachability is limited only by the
  provider's allowlist and TLS plus credentials. Restrict the Neon role to the API's database.
- Migrations (`alembic`) run against the same URL with the same rules.

## Consequences
- The primary and the backup API can share one managed database; it is no longer a single host
  failure point (the database is still a single managed service: see ADR-163 for replication).
- Existing loopback deployments do not change.
- `scripts/activate_beta_node.py` and other ops scripts still use the loopback-only helper
  (`parse_local_database_url`). They are not used for Neon and were not changed.

## Tests
- `services/api/tests/test_database_config.py`: accepts a Neon-style URL with `sslmode=require` and
  `channel_binding=require`, accepts `verify-full`/`verify-ca`; rejects `disable`, `prefer`, `allow`,
  `hostaddr`, `service`, `sslrootcert`, weak `channel_binding`, IP-literal and malformed remote hosts,
  and query parameters on loopback. Builds a lazy remote engine without connecting.
- Full API suite (repo root, project virtualenv): 726 passed, 413 skipped. The skipped tests need
  `VPN_TEST_DATABASE_URL` (a real PostgreSQL) and were **not run**.

## Not verified
- No connection to a real remote PostgreSQL or to Neon. The libpq `sslrootcert=system` behaviour and
  `channel_binding` with the bundled psycopg have not been run.
- No migration of the production database (see the README migration steps).
- No device or server test.

## Follow-ups
- Run the real-PostgreSQL test suite against a Neon **test** branch before any production use.
- Decide whether to make `verify-full` mandatory for remote hosts (stricter than `require`).

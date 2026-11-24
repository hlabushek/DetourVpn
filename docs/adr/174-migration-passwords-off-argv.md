# ADR-174 — The Neon migration keeps passwords off the command line

**Status:** accepted. Code and tests committed. **Not run on any host.**

## Context
`scripts/migrate_db_to_neon.sh` passed `SOURCE_DATABASE_URL` and `TARGET_DATABASE_URL`, both with
passwords, as arguments to `pg_dump`, `pg_restore` and `psql`. Arguments are visible to every
local user in the process list for as long as the dump and restore run. Found by the
2026-11-22 audit (M10).

## Decision
1. New helper `scripts/pg_conninfo.py <ENV_VAR_NAME> <PGPASS_FILE>`: reads the URL from the
   named environment variable (never from argv), appends the password to a pgpass file created
   with mode 0600, and prints a password-free libpq conninfo
   (`host=… port=… user=… dbname=… [sslmode=…] [channel_binding=…]`). Only `sslmode` and
   `channel_binding` query keys pass; anything else (`hostaddr`, `options`, …) is refused with a
   fixed code. Nothing secret is printed.
2. The script creates a private temporary directory, sets `PGPASSFILE` to a file in it, removes
   it on exit (`trap`), builds both conninfo strings, unsets both URL variables, and calls the
   PostgreSQL tools with `--dbname <conninfo>` only.
3. The existing guards stay (direct endpoint, `sslmode=verify-full`, `require` refused).

## Consequences
- The host needs `python3` (present on Ubuntu) next to the PostgreSQL client.
- The passwords live in a 0600 file for the duration of the run, readable by the same user
  and root only.

## Tests
- `services/api/tests/test_pg_conninfo.py`: conninfo without the password; pgpass escaping of
  `:` and `\`; refused URLs return fixed codes; the CLI prints no secret and writes the file
  (mode 0600 checked on POSIX); the script passes no URL to any PostgreSQL tool and exports
  `PGPASSFILE`. `bash -n` syntax check passed.

## Not verified
- The script was not run against a real source or Neon database.

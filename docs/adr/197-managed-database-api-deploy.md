# ADR-197 — API deploy to a beta host whose API reads the managed database

**Status:** accepted. Code and focused tests committed locally. **Not run against any host or
database.** Extends ADR-176 (which only refused); does not change the local Docker path.

## Context
The beta API now reads a managed PostgreSQL over TLS `verify-full` (ADR-166). A local Docker
database still exists on the host. `scripts/deploy_beta_api_latest.py` dumps, migrates and checks
the local Docker database only, so ADR-176 made it refuse such a host
(`refused=database_not_local_docker`). The beta.37 release needs the newer API code (signed
manifest download route) and migrations 0028 and 0029 on the database the API really uses.

## Decision
A second mode, `--database managed`, in the same script. The default (`--database local`) is
unchanged line for line, including the ADR-176 guard.

1. **Which database.** The remote preflight reads only the shape of `VPN_DATABASE_URL` in the
   host's API environment file with `grep` (nothing is printed). Local mode keeps the ADR-176
   pattern. Managed mode requires exactly one `VPN_DATABASE_URL` line, a DNS host name (not an IP,
   not loopback), and `sslmode=verify-full` (optionally `channel_binding=require`); otherwise
   `refused=database_url_ambiguous` or `refused=database_not_managed`. A host whose database is
   neither shape is refused in both modes before anything changes.
2. **Backup = owner snapshot.** A managed database cannot be dumped and restored by this script
   safely (no local superuser, credentials stay on the host). The owner creates a point-in-time
   branch/snapshot in the provider console right before the run and passes its identifier with
   `--snapshot-ref`. Managed mode refuses without it. The identifier is checked for shape only
   (letters, digits, `.`, `_`, `-`; 3–100 characters, so a pasted URL or password is refused); it is
   printed in the local plan as the record and is never sent to the host. The script cannot verify
   that the snapshot exists.
3. **Migration from the host's own API environment.** A helper, `scripts/managed_db_step.py`
   (taken from the deployed commit, uploaded to the run's private staging directory and checked by
   SHA-256), runs under `systemd-run` with `EnvironmentFile=` set to the API's environment file and
   the host's API virtualenv. It builds the engine with the API's own `Settings` and
   `create_database_engine` (verify-full, the API's CA rules). No database URL is passed on a
   command line, written to a new file or kept in the repository. The helper prints only fixed
   tokens (revision ids, `yes`/`no`, `managed_migration_passed`/`..._failed stage=... error=<class
   name>`), never exception text, which can contain host names.
4. **Role check before any change.** When a migration is pending, the preflight asks the helper
   whether the API's role can act as owner of every table in `public`
   (`pg_has_role(current_user, relowner, 'USAGE')`). If not, `refused=managed_role_cannot_migrate`.
   The deploy never switches to another, more privileged credential.
5. **Timeouts.** Each migration already sets `SET LOCAL lock_timeout = '5s'`. The helper also sets
   session `lock_timeout = 5s` and `statement_timeout = 120s`, so no step can wait unbounded on a
   lock held by another client of the shared database. Alembic upgrades to the exact target
   revision (not `head`), each migration in its own transaction, then `alembic check` must pass.
6. **Order and rollback.** Preflight → copy old source files → stop API → install new files →
   migrate → start API → readiness 200 → `/nodes` answers 401. On any failure after the change
   starts, the old code is restored and the API restarted (same as local). **The schema is not
   downgraded automatically:** 0028 only widens a check constraint and 0029 only adds indexes, so
   the old code works on the migrated schema. Both migrations have downgrades (0029 drops the
   indexes; 0028 refuses while `not_opened` receipts exist). A schema rollback is owner-only: restore
   the snapshot from step 2 in the provider console, or run the downgrade with an owner credential.
   A failed migration leaves no half-applied migration (transaction per migration).
7. **Owner gates.** `--execute` without `--database managed` behaves as before. In managed mode,
   `--execute` prints the full plan and then requires the owner to type `MIGRATE` in an interactive
   console; anything else, or no console, cancels with nothing changed.

## Owner steps (in order)
1. Dry run: `uv run --frozen python scripts/deploy_beta_api_latest.py --database managed
   --snapshot-ref <ID> --baseline-rev 8770b78 --before-revision 0027_panel_server_sources`
   (any shaped placeholder id works here; no SSH is made). Check the printed plan.
2. In the provider console: create a branch/snapshot of the beta database now; copy its id.
3. Run the same command with the real id and `--execute`; read the plan; type `MIGRATE`.
4. Expect `rollout=success`, `database_revision=0029_window_hot_path_indexes`. On
   `refused=managed_role_cannot_migrate`: apply 0028/0029 with an owner credential
   (`scripts/apply_managed_migrations.py`, Neon runbook step 2), then rerun with
   `--before-revision 0029_window_hot_path_indexes` (no migration pending, no role check).
5. Then publish without the API step: `scripts/enable_app_update.ps1 -Publish` without
   `-ApiBaselineRev` (that flow always runs local mode and would be refused).
6. Keep the snapshot until the beta has run on the new code; delete it in the console afterwards.

## Risks the owner accepts
- Writes between the snapshot and the migration are lost if the snapshot is restored.
- Another API on the same managed database (HA backup) keeps running old code during and after
  the deploy; it must tolerate the new schema (it does for 0028/0029, not checked on a host).
- `alembic check` drift on the managed copy (for example from the earlier restore) fails the run
  after the migration; the code is rolled back and the schema stays migrated.
- The pooled endpoint is not refused; ADR-166 recommends the direct endpoint.
- If the rollback runs when the schema is already at the target, the new migration files stay on
  the host (the existing rule removes them only at the old revision).

## Not verified
- No run against the host or the managed database: the role check, the migration, the timeouts,
  the TLS connection from `systemd-run`, and the rollback are unexercised.
- Snapshot existence and restore are provider-console actions only.

## Tests
- `services/api/tests/test_deploy_beta_guard.py`: the managed guard accepts managed URL shapes and
  refuses local, IP, `require` and extra parameters; the local guard is unchanged; refusals come
  before the first change; the remote program passes `bash -n` in both modes; snapshot ids and
  migration targets are shape-checked; the helper prints no exception text.

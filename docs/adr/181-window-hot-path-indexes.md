# ADR-181 — Indexes for the window hot paths (migration 0029)

**Status:** accepted. Migration `0029_window_hot_path_indexes`, model declarations and tests in
the working tree, uncommitted. **Not applied to the managed database.**

## Context
`LedgerService.held_lots` filters `peer_window_allocations` by `user_id`, which had no index, so
every create, renewal, settlement and quota read scanned all users' allocation history.
`runtime_ready` (every poll, admission, catalog row and connection view) looks for live
connections of another runtime and for holds without a receipt. Latency grows with history and
feeds the poll timeout (audit H2). Found by the 2026-11-22 audit (H3).

The migration is numbered 0029, not 0028 as first planned: 0028 is the `not_opened` receipt
constraint of ADR-179, written first. Commit ADR-179 before this ADR.

## Decision
1. `ix_peer_window_allocation_user` on `peer_window_allocations(user_id)`.
2. `ix_peer_connection_live_runtime` on `peer_connections(node_id, runtime_id)
   WHERE state <> 'closed'` for the live-connection half of `runtime_ready`.
3. Both are declared in `db/models.py` so `alembic check` reports no drift. Downgrade drops
   both. Plain `CREATE INDEX` inside the migration transaction (not `CONCURRENTLY`), with a
   5 s `lock_timeout`; the beta tables are small.

## Not solved here
- The hold half of `runtime_ready` (holds of other runtimes on the node without a receipt) is an
  anti-join over the node's whole hold history; `ix_peer_window_hold_runtime` exists but cannot
  avoid the scan for `runtime_id <> current`. Making it cheap needs either a persisted
  "unsettled" marker (holds are immutable by trigger, so a separate table) or a bounded query.
  **Owner decision needed** before that change: it touches the immutable funding schema.

## Owner steps
1. Apply on the managed database after ADR-179's migration: `alembic upgrade head` with the
   migrator role (the deploy script refuses a managed database, ADR-176, so this is a manual
   step with its own runbook).
2. Scripts that pin the deployed revision (for example `scripts/enable_app_update.ps1`,
   `--before-revision 0027_panel_server_sources`) must be updated when the deployed revision
   changes.

## Tests
- `services/api/tests/test_window_hot_path_indexes.py` (PostgreSQL): upgrade creates both
  indexes, downgrade to 0028 removes them, re-upgrade restores them, `alembic check` finds no
  drift, the live-connection index is partial; the planner can use the user index for the
  `held_lots` filter.
- `test_migrations.py` (head computed, all migration tests pass).

## Not verified
- No measurement on the managed database; no query-time comparison before/after.

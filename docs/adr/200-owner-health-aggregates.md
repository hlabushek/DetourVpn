# ADR-200 — Owner health panel: aggregate counts endpoint

**Status:** proposed 2026-11-27 (overnight run, step B2). Implemented and tested on a disposable
local PostgreSQL only. **Not deployed**; no real database was touched; no migration.
**Builds on:** ADR-110/111 admin panel and `/api/admin/v1/nodes`, I-030 read-only owner status,
`vpn_api.nodes.catalog.HEARTBEAT_SECONDS`.

## Problem

The owner has per-user and per-node admin views, but no single read that answers "is the beta
healthy right now" without opening individual users or node rows.

## Decision

New admin route `GET /api/admin/v1/health-summary` (response model `AdminHealthSummary`), same
authentication as every other admin read (admin session with MFA; `401` without it).

It returns **counts only** (integers ≥ 0, a fixed set of fields, `extra="forbid"`):

| Field | Meaning |
|---|---|
| `users_active`, `users_pending`, `users_disabled` | users by status (deleted are not counted) |
| `devices_active`, `devices_revoked` | devices without / with `revoked_at` |
| `nodes_active`, `nodes_disabled` | VPS nodes by status |
| `nodes_active_heartbeat_fresh` | active nodes seen within `HEARTBEAT_SECONDS` (the catalog's own "available" heartbeat rule) |
| `connections_pending`, `connections_active`, `connections_revoke_pending` | peer connections that are not closed, by state |
| `tunnels_not_revoked` | legacy tunnels not revoked |
| `commands_unacknowledged`, `commands_stalled` | current-version peer commands not acknowledged; of those, older than 90 s (same rule as `/nodes`) |

- No identifiers, names, regions, addresses, DNS, URLs, keys, device data, timestamps or
  per-user rows. Nothing is grouped by user, device or node.
- Read-only: plain `SELECT count(*)` queries in one transaction, no row locks, no writes, no audit
  row (it is a read like `/nodes`).
- **No new table and no Alembic migration**: every count comes from existing tables and columns.
- OpenAPI (`packages/contracts/openapi.json`) re-exported with `scripts/export_openapi.py`; the
  diff only adds this path and its schema.

## Security and privacy

- Admin-only, behind the existing MFA session; no new role, permission or public surface.
- Counts can still be revealing in a very small beta (for example "1 active connection"). This is
  acceptable for the owner's own admin panel; do not expose this endpoint or its numbers publicly.
- No telemetry is collected that did not exist before; this only counts existing rows.

## Not done / not verified

- No admin-panel UI change (the panel does not call it yet).
- Not deployed to beta; not run against a real or staging database.
- Query cost on a large database not measured (beta tables are small; the counts use existing
  indexes where present).

## Verification

`python scripts/test_with_postgres.py --postgres-bin <portable pgsql bin> --
services/api/tests/test_admin_health.py`: 5 passed (exact counts, no identifiers or names in the
body, all-zero empty state, `401` without or with a bad token, read-only and documented in
OpenAPI). Ruff, ruff format and mypy clean on the changed files. Wider results are in the morning
report.

# ADR-179 — A gate that never opened releases the budget hold ("not_opened" receipts)

**Status:** accepted. Code, migration `0028_window_not_opened_receipts` and tests in the working
tree, uncommitted. **Not deployed; the migration was not applied to the managed database.**

## Context
The node wrote an `unknown` receipt whenever it could not measure a window, including windows
whose gate was never opened. `held_lots` keeps the full budget of an `unknown` window held until
an admin resolution, and the only resolution kind is `charge_full_budget`. So every grant that
never reached an open gate either locked up to 64 MiB of the user's quota indefinitely or was
charged in full for traffic that could not have passed. Triggers that are not the user's fault:
a renewal that arrives too late, node clock skew (`grant_clock_skew`), `dispatch_deadline_exhausted`,
a node restart while a grant was only an intent, and a revoke of a grant the node never applied.
Found by the 2026-11-22 audit (H4).

## Decision
1. **New receipt observation `not_opened`** (`ClosedWindow.observation`), with
   `observed_bytes = null` and no ledger operation. It is a contract addition; `measured` and
   `unknown` are unchanged.
2. **Server.** `held_lots` and `reserve_window` already treat only `unknown` as held or
   unresolved, so a `not_opened` receipt releases the hold, charges nothing and closes the
   connection. The receipt row is kept for audit. Migration `0028_window_not_opened_receipts`
   widens the `ck_peer_window_receipt_evidence` check; its rollback refuses while any
   `not_opened` receipt exists.
3. **Node (`window_runtime.py`).**
   - A durable marker (`opening` table in the node journal, created if missing) is written
     right before `gate.open()`. It is written after `install_closed`, so a marked window's
     counter belongs to that grant.
   - `_finish`: state `active`, or `intent` with the marker, is "opened": measured when the
     counter is available, else `unknown` (unchanged rule). State `intent` without the marker
     was never opened: `not_opened`.
   - `close_grant` of a grant that this runtime never saved (declined by contract checks, late,
     clock-skewed, or revoked before dispatch): `not_opened`.
   - `recover` (grants of an older runtime that the journal has no receipt for) stays
     `unknown`: if the journal file was lost or replaced, the node cannot prove the gate never
     opened.
4. Side effect, deliberate: a crash after `gate.open()` but before the `active` commit now
   yields a `measured` receipt (the counter was reset for this grant) instead of `unknown`.

## Owner decision needed
- **Recovery after journal loss** stays `unknown` (held until an admin resolves it). If the
  owner accepts that a node's journal is never replaced without a full drain, recovered grants
  with no journal trace could also be `not_opened`. Default kept: `unknown`.
- **Crash between `gate.open()` and the marker**: impossible by construction (marker first).
  A crash after the marker but before `open()` is reported as opened (measured, usually 0
  bytes). Conservative; no change proposed.
- **Existing `unknown` receipts** already in the database are not converted. The owner decides
  per receipt with the existing admin resolution (or a later tool).

## Rollout order (required)
1. Apply migration 0028 on the managed database and deploy the API.
2. Only then update the node agents. An old API rejects a `not_opened` receipt (pydantic),
   which fails that receipt only (ADR-173 isolation), but the node keeps resending it and the
   slot stays blocked until the API is updated.

## Tests
- `services/api/tests/test_window_not_opened.py`: node side — revoke of a never-dispatched
  grant, clock-skewed grant, dispatch deadline before open, restart during intent without the
  marker are all `not_opened`; an opened gate is still `measured`; recovery stays `unknown`.
  Server side (PostgreSQL) — a `not_opened` receipt releases the hold with no charge while a
  measured one in the same poll charges; ledger reconciliation stays consistent; the migration
  rollback refuses while a `not_opened` receipt exists.
- `test_window_runtime.py`: two expectations updated (late renewal → `not_opened`; crash after
  open → measured).
- `test_migrations.py`: the head revision is computed instead of pinned to 0027.

## Not verified
- No real node, kernel counters or managed database.

# ADR-082 — audited resolution of an unknown beta window

Status: accepted for isolated beta on 2026-11-04 under the owner's delegated
authorization to make decisions needed to accelerate beta launch. This does not
authorize any production action.

## Context

On 2026-11-04 beta recorded one immutable `unknown` receipt for generation 2,
slot 1, retaining a 1 MiB funding hold. Read-only evidence is in
`../evidence/i-030-beta18-unknown-diagnostic-20261104.json`.

The node process has been active since 2026-10-26 with zero systemd restarts;
the generation-2 hold was created on 2026-11-04. Startup recovery and
`counter_exists=false` at startup therefore do not explain this receipt. The
same connection's generation 1 settled as `measured`; its receipt was accepted
4 ms before generation 2's `unknown`, while 61.62 seconds remained before
generation 1's wall-clock deadline. This strongly suggests both receipts were
accepted in one serialized node poll. A revoke of pending generation 2 and an
apply failure before the generation-2 journal row became `active` can both
produce this sequence. A later close after generation 2 became active with a
silent `closed_bytes()` failure or counter above budget remains possible: the
1.61-second hold-to-receipt interval can contain another one-second poll. The
poll command/outcome was not persisted, so the exact branch cannot now be
proven. The node does not persist the historical counter or ACK timestamp, and
`closed_bytes()` failures are swallowed. Generation 1's measured counter was
below half its budget and below the full budget; the client-side advisory
counter is a separate statistic and was not persisted.

The current code intentionally preserves the hold: `PeerWindowReceipt` is
immutable, `settle_window()` rejects a conflicting replacement, and
`LedgerService.held_lots()` continues to reserve the full allocation for an
unknown receipt. Generic admin credit/debit actions do not release this hold.
ADR-038 requires reviewed, audited, idempotent operator reconciliation or
compensation, but no such resolution flow is implemented.

## Decision and mechanism

Keep the original unknown receipt byte-for-byte immutable. Add a separate,
append-only resolution record bound to the exact hold, owner, administrator,
idempotency key, fixed resolution kind, bounded reason, evidence digest,
amount and database timestamp. Require an authenticated MFA administrator,
an explicit reason, a closed connection, closed gate evidence, and a
transaction that locks the owner and hold. The record must never describe its
amount as measured traffic.

For the current incident, the evidence reference is
`docs/evidence/i-030-beta18-unknown-diagnostic-20261104.json` with SHA-256
`a31fadef09f55df3e0121df5ba5651175c617aebe17259c47a9a4bb89ae164f0`.

Choose `charge_full_budget`: post a debit equal to the original hold amount
against its pinned funding allocation as an adjudicated unknown-window
compensation, not as `peer_window` measured usage. Release the hold in the same
transaction and retain the unknown receipt and resolution audit. This is the
conservative, auditable way to close the current reservation when neither a
measured counter nor proof of zero traffic exists. A no-debit release is not
implemented; it would require independent retained evidence that the unknown
generation could not have passed traffic.

Both operations must be idempotent and immutable; conflicting retries fail
closed. Ledger reconciliation must verify the resolution, pinned allocation,
operation source and amount, and must flag unresolved unknowns separately from
resolved unknowns. No direct SQL edits, receipt replacement, or generic admin
adjustment can substitute for this flow.

## Incident decision

The exact node command outcome is unavailable. The evidence supports neither a
measured byte count nor proof that generation 2 never opened. The owner delegated
the accounting decision; therefore this incident is to be charged for the full
1 MiB hold as a separate adjudicated compensation. The immutable node receipt
remains `unknown` and `observed_bytes=null`.

No further physical Connect or tester admission was allowed until the resolution
was implemented, verified, and the read-only status was `closed`, zero
non-closed connections, zero reserved bytes, and a closed revoke gate.

## Implementation and verification

- Add an Alembic migration for the append-only resolution record; downgrade must
  refuse to delete any resolution or its ledger operation.
- Add the MFA/admin role path, stable errors, idempotent transaction, exact
  pinned-lot debit and reconciliation support.
- Verify replay, conflicting requests, closed-connection guard, immutable
  unknown receipt, released hold and consistent ledger on real disposable
  PostgreSQL before deployment.
- The implementation passed the focused disposable-PostgreSQL verification
  recorded in I-030 and was deployed to the isolated beta control plane at
  schema revision `0018_peer_window_resolution`. Readiness returned 200 and an
  unauthenticated action probe returned 401. This deployment did not change
  quota or resolve the incident; its sanitized receipt is
  `../evidence/i-030-unknown-resolution-beta-deploy-20261104T152445Z.json`.
- The owner applied the selected resolution exactly once through the MFA admin
  endpoint. Read-only database evidence confirms one full-budget resolution,
  one applied debit, the unchanged unknown receipt, and zero unresolved bytes;
  the authenticated API readback confirmed `closed/0/0` and a closed revoke
  gate. Receipt: `../evidence/i-030-unknown-resolution-beta-20261104T161109Z.json`.
- This accounting incident is resolved on isolated beta. The historical node
  branch remains unproven, and this resolution did not perform a physical
  Connect or establish tunnel acceptance. Do not touch production.

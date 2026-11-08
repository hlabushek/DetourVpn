# ADR-089 — resolution of the beta.22 unknown window (16 MiB)

Status: applied 2026-11-08, after the owner's explicit go-ahead in chat and
the owner's own MFA entry. Isolated beta only, no production action.

## Context

ADR-088 attempt 1 (LTE) left `reserved_bytes=16777216` and `revoke_gate=open` for ~12 h.
Read-only database diagnostic (`../evidence/i-030-beta22-unknown-diagnostic-20261108.json`,
SHA-256 `461d5458c0527aa2f04e19d9e7e8bcd50f9ce30f892d9418cb37b50221cc0ea1`):
exactly one unresolved `unknown` receipt (generation 2, slot 1, 16 MiB, receipt accepted
2 s after the hold, in the same poll as generation 1's measured receipt). Connection is
closed, no hold lacks a receipt, all later holds are `measured`. Same pattern as ADR-082.

## Decision

Reuse the ADR-082 mechanism unchanged: `resolve_unknown_window` with `charge_full_budget`,
debiting the exact 16 MiB against the pinned allocation, releasing the hold in one
transaction, keeping the unknown receipt immutable. A no-debit release stays unimplemented:
there is no counter and no independent proof of zero traffic.

`scripts/i030_resolution_owner_beta22.py` is a separate script (the ADR-082 script stays
bound to its 1 MiB incident). It verifies the evidence file hash locally, requires owner MFA,
requires pre-state `closed/0/16777216`, requires exactly one unresolved unknown window matching
what the server accepts, then audits the ledger read-only afterwards. Login retries only while
connecting (before any request byte is sent), so a TOTP is never replayed.

## Consequences and limits

- 16 MiB is permanently charged to the owner test account (about 3% of its granted quota).
- The charge is compensation, never reported as measured traffic.
- Root cause of the `unknown` (hang of the first LTE connection) remains open; the ADR-088
  series stays paused at attempt 1 until postflight shows `reserved_bytes=0`, `revoke_gate=closed`.

# ADR-182 — Link-code redeem takes the beta invite before the user row

**Status:** accepted. Code and a PostgreSQL test in the working tree, uncommitted. **Not
deployed.** A separate ADR (not an ADR-167 amendment): ADR-167 is about window connections; this
pair is about invites.

## Context
`redeem_link_code` locked the user row `FOR UPDATE`, then the link code, and only then checked the
beta invite (`beta_subject_allowed`, `beta_invites … FOR SHARE`). The admin `revoke_beta_invite`
locks the invite `FOR UPDATE`, then the identity, then the user row. A redeem and a revoke of the
same subject at the same moment could deadlock. `authenticate()` already used the invite → user
order. Found by the 2026-11-22 audit (L2, a rare pair).

## Decision
`redeem_link_code` reads the identity (no lock), runs `beta_subject_allowed` (invite `FOR SHARE`
when invites are enforced), then locks the user row, then the link code. The global order for
these rows is now the same everywhere: beta invite → user → (code, family, devices). The
response for every refusal stays the same generic `invalid_link_code`.

## Tests
- `services/api/tests/test_link_redeem_lock_order.py` (PostgreSQL): the statements of a real
  redeem are recorded; the invite check comes before the user row lock, which comes before the
  code lock. The test harness does not enforce invites (`beta_invites_only` is off), so it
  proves the call order, not a `FOR SHARE` lock wait. No two-transaction deadlock test.
- Related: `test_device_links.py`, `test_device_link_codes_unit.py`, `test_admin.py`.

## Not verified
- No reproduction of the deadlock itself and no managed database.

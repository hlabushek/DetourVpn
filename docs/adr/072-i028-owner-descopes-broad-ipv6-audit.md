# ADR-072 — Remove the broad DNS/IPv6 audit from owner-scoped I-028 completion

Status: accepted by the owner on 2026-10-31.

## Context

The owner wants the Android VPN profile to use IPv4 and block included IPv6
traffic, matching the behavior accepted for personal use. The source-bound
Egress run on 2026-10-31 passed the scoped fixed-socket IPv6-block checkpoint
in ADR-071, with a synchronized AP-station capture and successful cleanup.
The remaining broad DNS/IPv6 packet audit has been consuming disproportionate
time and is no longer wanted as an I-028 completion step.

## Decision

- Remove comprehensive DNS/IPv6 packet auditing from the remaining owner-scoped
  I-028 acceptance checklist. Do not repeat the AP capture or add IPv6-specific
  physical tests for this checkpoint.
- Keep the current IPv4-only VPN profile and its included-IPv6 blocking
  behavior. No tunnel redesign or route change is requested by this decision.
- Treat the passing ADR-071 Egress receipt as the accepted bounded personal
  staging result. It covers the fixed test socket and run only; it does not
  assert device-wide leak freedom or comprehensive DNS privacy.
- This decision does not amend the broader product/release requirements in
  `TECHNICAL_SPEC.md` or authorize a public leak-free claim.
- The only remaining I-028 physical gate, assuming the other recorded passes,
  is independent-provider failure. Use an existing independently hosted test
  resource if available. No new purchase or infrastructure deployment is
  authorized by this ADR.

## Consequences

The broad audit described in ADR-064 is descoped from this personal I-028
checkpoint, not passed. ADR-064 remains a reference for any later product
security/release audit. I-028 can close for this owner's staging scope after
the independent-provider criterion passes or is explicitly waived; product
release readiness remains governed by `TECHNICAL_SPEC.md`.

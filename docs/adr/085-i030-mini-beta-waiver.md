# ADR-085 — scoped waiver for a closed mini-beta of up to five friends

**Status:** accepted for the isolated beta by the owner on 2026-11-07.
**Date:** 2026-11-07
**Issue:** I-030

## Context

The pre-beta plan and the I-030 notes make Phase 6 evidence and the packet-level
DNS/IPv6 audit gates before inviting friends. The Phase 6 evidence is SLO, load
and usage lag, memory and battery baseline, node-rotation drill, free-traffic
cost and a full SBOM. The packet audit needs a capture point before the router's
NAT, which ADR-072 deferred. The owner wants to start the beta quickly with a very
small group of known friends.

## Decision

Defer these gates for a closed beta limited to at most five invited friends:

- Phase 6 measurements and the complete SBOM, licence and toolchain vulnerability
  review (the Flutter SDK still declares Kotlin Gradle plugin 2.2.20);
- the pre-NAT DNS/IPv6 packet audit;
- Always-on and Lockdown validation.

The waiver holds only while all of these hold:

- Invites are issued individually through the MFA admin invite flow; no public link.
- Each tester receives a 5 GiB credit by an idempotent MFA admin `credit` with a
  recorded reason. No payment is involved.
- Testers are told in writing that the beta is experimental: no sensitive traffic,
  no kill switch, no leak protection, one node without independent monitoring,
  IPv4 only for included apps with IPv6 blocked, and brief pauses at window changes.
- Nothing claims guaranteed bypass, Always-on, Lockdown or complete privacy.
- Access can be withdrawn at once with `revoke_beta_invite`, and a window or
  hold in an `unknown` state is resolved through the audited ADR-082 flow.

These gates stay mandatory before the first tester connects:

- owner-observed handshake, source-bound egress, disconnect, server `closed` and
  ledger reconciliation on beta.22 (ADR-084);
- a current backup with a restore drill, and the I-027 scheduled-run status recorded;
- invite grant and revoke verified on the hosted beta admin panel;
- the first-tester run from `closed-beta-first-tester.md` recorded in a sanitized receipt.

## Consequences

The waiver is a risk acceptance, not a pass: every deferred item stays open in
I-030 and must be closed before widening the group, publishing outside the private
channel, or removing the tester warnings. Any critical vulnerability found in the
shipped runtime, a leak, or a confirmed accounting error ends the waiver.

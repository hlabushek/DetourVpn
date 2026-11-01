# ADR-071 — IPv4-only I-028 staging with included IPv6 blocked

Status: accepted by the owner for the bounded personal staging checkpoint on
2026-10-31. The scoped same-port physical probe and correlated AP capture passed
on one Android device. This is not a public product privacy or
release-readiness claim.

## Context

The owner accepts IPv4 VPN egress and wants IPv6 unavailable to traffic routed
through the full-tunnel VPN profile. The earlier decision accepted that IPv6
could use the ordinary network; the owner has now superseded that choice and
requires included IPv6 traffic to be blocked. IPv6 is not translated into IPv4
and is not carried inside the tunnel.

Earlier Egress receipts passed their scoped IPv4, fixed UDP DNS, same-UID
included/excluded, renewal, accounting, and cleanup checks, but the hostname
IPv6 probe failed at DNS resolution. Those receipts do not prove IPv6 blocking.
The AP-station captures saw the VPN outer flow and had an excluded positive
control, but aggregate IPv6 traffic cannot be attributed to the included app.

## Decision

- For the owner's bounded I-028 checkpoint, require a direct phone baseline
  that can open a fixed public IPv6 literal over TCP, with VPN off.
- During the included full-tunnel app probe, require validated VPN state,
  staging IPv4 egress, and the fixed UDP DNS control to succeed, while a direct
  TCP connection to the same fixed IPv6 literal does not connect. A specific
  no-route/network-unreachable result is sufficient. If Android returns a
  generic `io` result, require a direct positive control using the same local
  source port, an explicit confirmation that the included socket reached
  connect() after bind, and a synchronized pre-NAT AP-station capture that sees
  the VPN outer trigger, completes the 90-second window without packet-limit
  truncation, and records zero packets from that fixed IPv6 source port. DNS
  failure, timeout, connection refusal, failed bind, or a missing/invalid
  capture remains inconclusive and fails this criterion.
- This behavior applies to apps included in the full-tunnel VPN profile.
  Apps explicitly excluded from the VPN continue to use the ordinary network
  and are outside this block guarantee.
- Keep the existing Android DNS settings unchanged. The scoped fixed DNS probe
  is not a comprehensive DNS privacy audit.
- ADR-072 removes ADR-064's broad packet audit from this personal I-028
  checkpoint. The scoped probes do not establish UID-wide leak-freedom or cover
  every transition/failure.
- Do not use this personal staging decision to claim leak-free operation,
  release readiness, or equivalent behavior for other users. I-028 remains
  open for independent-provider failure; no target is authorized here.

## Consequences

The 2026-10-31 source-bound Egress run passed the bounded included-IPv6 block
criterion. The direct same-port positive control connected, the included probe
confirmed it reached `connect()` and did not connect over IPv6 (`io`), and the
synchronized 90-second AP-station capture saw the VPN outer trigger with zero
packets from the fixed probe source port. The same run passed staging IPv4,
fixed UDP DNS, settlement, and cleanup. The operator-pasted capture also saw
four aggregate IPv6 packets whose app/UID attribution is unknown. This result
covers one fixed probe socket and one bounded staging run; it does not establish
device-wide leak freedom, comprehensive DNS privacy, or behavior for other
destinations, apps, transitions, or failures. The Egress receipt remains
`diagnostic_only=true` and `vpn_dataplane_acceptance=false`. Per ADR-072, the
broad DNS/IPv6 audit is no longer an I-028 personal-checkpoint gate. Overall
I-028 remains open for independent-provider failure.

Evidence: `docs/evidence/i-028-egress-diagnostic-20261031T182523Z-91d07c8e.json`
and `docs/evidence/i-028-ipv6-included-block-20261031T182345Z.json`.

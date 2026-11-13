# ADR-137 — Route and DNS expectations of the local config; no leak claim

**Status:** accepted 2026-11-13 (connection reliability run 3, item E; follow-up to ADR-132).

## Context
ADR-132 states that IPv6 and DNS leaks are expectations from code and were never measured. Run 2 could
not unit-test the route and DNS builders, because `LibboxConfigFactory` builds JSON with `org.json`,
which is an Android stub on the JVM.

## Decision
- The fixed values the builder uses move to `RoutePlan`: the TUN route set (`0.0.0.0/0`, `::/0`), the DNS
  strategy (`ipv4_only`), and the DNS upstream choice (the first DNS server of the profile; `profile_invalid`
  if none). `LibboxConfigFactory` now reads these values from `RoutePlan`.
- `RoutePlanTest` proves these values: IPv4-only and dual-stack profiles, DNS choice order, and the empty-DNS
  rejection.
- **The JSON assembly is not unit-tested.** Adding `org.json` as a test dependency was not done: it is not
  needed to prove the policy values, and its license is not a plain permissive one. Packet captures on a phone
  are the next step.

## What the code does (expectations, not guarantees)
- The TUN captures IPv4 and IPv6 destinations whatever the profile says.
- Whether an IPv6 destination reaches the peer depends on the profile's AllowedIPs, copied unchanged.
- DNS answers are IPv4-only (`ipv4_only`). AAAA lookups get no synthetic answer, so a name that has only
  IPv6 addresses may not resolve.
- Only the first DNS server of the profile is used as the upstream; later servers are ignored.

## Security considerations
- No setting is changed. The app and the report make no "no leak" claim.

## Limits / not verified
- Leak tests need a packet capture on a phone (IPv6, DNS, Lockdown). Not done.
- IPv6-only and NAT64 networks are not verified.

# ADR-132 — Routing, leak limits, Always-on and Lockdown

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2). Documentation only.

## Data path (from code)
- Routes: `AllowedIPs = 0.0.0.0/0` from the API profile; one peer, so `allowFamily` is **not**
  called. Android's `VpnService` then blocks IPv6 for apps routed into the VPN (no IPv6 route,
  family not allowed): IPv6 traffic of those apps is dropped, not sent directly.
- DNS: the profile's DNS or the user's choice (ADR-106), applied as `addDnsServer` on the TUN.
- Transport sockets: `protect()`ed right after `awgTurnOn`; a failed protect tears the tunnel
  down (`socket_protection_failed`). They never route into the TUN.
- Split tunnelling: "all" and "exclude" add the app's own package to `ExcludedApplications`;
  "include" lists only the chosen apps. The app's own API/renewal traffic therefore goes direct
  (by design; it carries no browsing data). Health checks add no traffic (ADR-128).
- During `restartInPlace` the new interface replaces the old one via `establish()` on the same
  service; the VPN is never torn down, so routed apps do not fall back to the direct network.

## Always-on and Lockdown
- Always-on is declared (`SUPPORTS_ALWAYS_ON=true`) and handled by the restore path
  (ADR-098/121). It works with windows because each system start leads to a fresh window.
- Lockdown ("block connections without VPN") is enforced by Android, not by the app. Under
  Lockdown, excluded apps — **including this app** — are blocked unless the system allows the
  VPN app's own traffic; native renewal is therefore skipped under Lockdown and Dart renews
  through the protected relay (ADR-121). Not changed.
- Without Lockdown, any period with no VPN interface (window expired and not yet recovered,
  process killed, user disconnect) lets apps use the direct network. App logic cannot prevent
  that; only system Lockdown can. No setting claims otherwise.

## Not verified
No leak test was run (no device, no packet capture). "No IPv6 leak" and "no DNS leak" are
expectations from the code above, not measurements.

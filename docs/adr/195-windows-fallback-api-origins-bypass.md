# ADR-195 — Windows routes fallback API origins around the tunnel

**Status:** implemented on branch `claude/connection-reliability` (committed locally, not pushed).
**Unit-tested only (Dart channel arguments, native validation, and the engine config produced for
the fake engine). No real Windows run, no real multi-origin API.**
**Issue:** I-032, gap W-05. **Builds on:** ADR-161 (fallback API origins), ADR-095 (Windows API
bypass rule), ADR-132 (Android excludes the app from its own VPN).

## Context
ADR-161 lets `Api` try up to two fallback origins after the primary. On Android the whole app is
excluded from its VPN, so every origin, primary or fallback, is reached directly. On Windows only
the addresses of the **primary** origin (from the stored session) had a `direct` route rule. A
fallback host was therefore reached **through** the tunnel. That fails exactly when a fallback is
needed: the tunnel is broken or the node is unreachable.

## Decision
1. **Dart → native contract.** `PlatformTunnel` takes `apiFallbackHosts`: the lower-case host names
   of `Api.origins` after the primary (`fallbackApiHosts`). `main.dart` fills it only on Windows.
   When the list is non-empty, `connect`, `connectWindow` and `connectVless` send it as the
   argument `apiFallbackHosts`. When it is empty (Android, or a Windows build without
   `VPN_API_FALLBACK_ORIGINS`), the arguments are byte-for-byte unchanged.
2. **Native validation, fail-closed.** `TunnelChannel` reads the optional list; a wrong type is
   refused like any malformed argument. `TunnelController::SetApiFallbacks` accepts at most two
   entries, each a lower-case DNS name (same rule as the VLESS server name) or an IPv4 literal, with
   no duplicates. Anything else gives `native_config_invalid` and the connect does not start. A
   missing argument resets the list to empty, so the primary-only behaviour is the default.
3. **Routing.** `ControlPlaneAddresses()` keeps the primary rules unchanged: it must resolve, with
   1–8 IPv4 addresses, else `native_config_invalid`. Then it adds each fallback's IPv4 addresses
   (literal, or resolved before the tunnel starts, at most 8 per host), without duplicates, to the
   same `ip_cidr → direct` rule. **A fallback that does not resolve, or resolves to something
   unusable, is skipped.** A fallback never blocks a connect that the primary allows. The policy
   cap `kMaxApiIpv4` is raised from 8 to 24 (3 × 8).
4. In-place restarts (ADR-194) reuse the policy built at connect, so they keep the same bypass
   list.

## Security and privacy
- Only build-time configured hosts can be added. The list comes from `Api.origins`, which ADR-161
  already validates (https only, no user info, path, query or fragment, no duplicates). Native
  validates it again and never accepts a URL, a port or an IPv6 literal.
- More direct addresses mean more destinations that bypass the tunnel. The bypass covers only the
  API hosts the app itself uses, exactly as for the primary. Other apps reaching those addresses
  also go direct, which is unchanged in kind from the primary rule.
- No logging of hosts or addresses.

## Known limitations (NOT verified)
- Addresses are resolved once, before the tunnel starts. If a fallback host's DNS answer changes
  while connected (CDN rotation), traffic to the new address goes through the tunnel until the next
  connect. This is the same limit the primary origin already has.
- An IPv6-literal fallback origin is refused on Windows (`native_config_invalid`). The Windows
  tunnel is IPv4-only and rejects IPv6 flows. Such a build cannot connect until the origin list is
  fixed.
- No real failover was observed; ADR-163's multi-instance API does not exist yet.

## Verification
- `flutter test` (full suite on an ASCII-path copy): 426 passed. New: `native_tunnel_test.dart`
  "fallback API hosts go with every connect only when configured".
- C++ `adapter_tests`: validation cases (more than two, upper case, URL, duplicate, IPv6); the
  config produced for the fake engine holds only the primary when none is configured; it holds
  primary + resolved fallback + literal fallback when configured; an unresolvable or junk fallback
  is skipped and the connect still succeeds. `config_factory_tests`: 24 addresses accepted, 25
  refused.

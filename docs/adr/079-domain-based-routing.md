# ADR-079 — Domain-based split routing requires a domain-aware data plane

Status: accepted for bounded implementation; pinned native AAR and Android compile
verified, runtime and physical-device acceptance remain open, 2026-11-03.

## Context

The current Android screen and AWG path select apps by package ID. Android's
VPN builder exposes per-app allow/deny lists and IP-prefix routes; the TUN
interface carries IP packets. It has no native rule that routes a hostname or
wildcard such as `*.ru` directly.

Mapping DNS answers to IP routes is not enough for reliable domain rules: one
IP may serve many unrelated sites, answers change by TTL, and apps may use
encrypted DNS or cached answers. A list-only UI would imply behavior the tunnel
does not implement and could send traffic through the wrong path.

## Product semantics for the requested UI

- Keep one split on/off switch and one direction selector shared by two
  horizontally swipeable tabs: **Домены** and **Приложения**.
- A selected app or selected domain uses the same direction. In `exclude`
  mode, either match bypasses the VPN and unmatched traffic uses the VPN. In
  `include` mode, either match uses the VPN and unmatched traffic goes direct.
  With split off (`all`), all ordinary app traffic uses the VPN.
- Match domain rules device-wide. Apps and domains combine with OR semantics;
  this lets a domain rule work from any app and avoids making app selection a
  prerequisite for domain selection.
- MVP entries are hostnames, not URLs with paths, ports, or schemes. Normalize
  case and IDNA locally, remove one trailing dot, reject IP literals and
  malformed labels. A suffix rule such as `*.example.ru` matches the apex and
  its subdomains, but not `notexample.ru`. IP/CIDR rules need a separate,
  explicitly labelled rule type.
- If the engine cannot identify a hostname, the base direction above applies;
  it must never guess based on a shared IP. Do not expose a rule-hit log or send
  domain names to the API. Encrypted DNS/ECH and cached answers can hide a name;
  explain that a matching rule cannot be guaranteed in those cases. Engine
  failure must stop the VPN rather than silently switch to direct traffic.

These defaults implement the requested shared toggle, include/exclude selector,
and swipeable Domains/Apps tabs. They are intentionally conservative: no
hostname guess from shared IPs, no remote rule storage, and no route when the
engine cannot safely determine a match.

## Upstream component evaluation

The existing `AwgProviderV1` / GoBackend path advertises AWG and full IPv4
route capabilities only. `ClientProfile` applies Android's package allow/deny
list and parses an AWG config; it has no domain-aware packet router.

| Candidate | Domain and Android app routing | AWG compatibility | License / decision |
|---|---|---|---|
| Current Android GoBackend | Package lists and IP routes only | Current AWG 3.1 path | Cannot implement hostname routing by itself. |
| Xray-core | Domain routing and Android TUN are documented | Its WireGuard outbound is standard WireGuard; no AWG 3.x support was found in its documented protocol list | MPL-2.0, but changing the remote transport would be a separate protocol decision. Do not replace AWG implicitly. |
| Amnezia Box | Sing-box routing supports domain and package matches; Android mobile library is documented | Amnezia fork documents an AWG endpoint and pins `amneziawg-go/v3` | Best technical fit. GPL-3.0-or-later plus an additional no-name/no-association term. Adoption requires an explicit distribution/source-compliance decision and a pinned immutable release. |

The owner approved GPL/source-compliance for this integration on 2026-11-03.
The selected upstream is Amnezia Box `v1.260910.0`, peeled commit
`16724484cfa00d9b2c14d8280dc055564e2e12ba`. The pinned Android AAR builds with
`with_awg,with_gvisor` for `arm64-v8a` and `x86_64`; its current SHA-256 is
`81820d063205f0dfbaa5431d2904e31d266a592d2df32e9389584499985c9473`. Release
Java and manifest compilation passed against that AAR. The regular Flutter build
script now rebuilds and stages this AAR in its isolated source snapshot. These
checks establish reproducible native compilation, not runtime behavior.

Implement one `TunnelProviderV1` adapter that owns the sole Android
`VpnService` TUN. It must not run beside GoBackend as a second VPN. Keep device
private keys local, map only validated server AWG fields into the engine config,
preserve the protected control-plane transport, and keep server-side byte
accounting authoritative. Do not send app lists or domain rules to the control
plane. The provider must preserve actual AWG peer handshake and receive-byte
signals used by existing lease acceptance; service-start time is not an
acceptable handshake substitute.

Before distributing the dependency, scan its complete transitive SBOM and
advisories, document all licenses, provide the exact corresponding source and
patches with the beta, include license notices, and avoid any branding that
implies Amnezia affiliation. The app's repository-wide license remains outside
this ADR; distribution must still satisfy GPLv3-or-later for the combined
client and the upstream additional name/association restriction.

## Remaining acceptance before beta use

On a physical Android device, test exact names, suffix/wildcard boundaries,
redirects, CNAMEs, shared CDN IPs, IPv4/IPv6, QUIC, system DNS and encrypted
DNS, cache expiry, network changes, rule precedence, and policy-engine failure.
Verify both intended egress paths without recording visited domains. Keep the
feature marked experimental until the real policy path and failure semantics
pass on the target device. Physical Android acceptance is required before
claiming the feature works on the target device.

## References

- [Android VPN guide: per-app VPN and app lists](https://developer.android.com/develop/connectivity/vpn)
- [Android `VpnService.Builder`: app lists and IP routes](https://developer.android.com/reference/android/net/VpnService.Builder)
- [RFC 8484: DNS Queries over HTTPS](https://www.rfc-editor.org/rfc/rfc8484)
- [RFC 7858: DNS over TLS](https://www.rfc-editor.org/rfc/rfc7858)
- [sing-box route rules](https://sing-box.sagernet.org/configuration/route/rule/)
- [Amnezia Box repository and license](https://github.com/amnezia-vpn/amnezia-box)
- [Amnezia Box Android mobile build](https://github.com/amnezia-vpn/amnezia-box/blob/master/docs/amnezia/build.md)
- [Amnezia Box consumer integration guide](https://github.com/amnezia-vpn/amnezia-box/blob/master/docs/amnezia/consumer.md)
- [Xray routing](https://xtls.github.io/en/config/routing)
- [Xray WireGuard outbound](https://xtls.github.io/en/config/outbounds/wireguard.html)

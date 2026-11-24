// Port of apps/client/android/.../LibboxConfigFactory.java for the Windows engine
// (detour-engine.exe, Amnezia Box). Builds the sing-box JSON config from an already validated
// profile and the local routing policy (ADR-079). The result contains the device private key:
// hand it to the engine on stdin and never log, print or persist it.
#pragma once

#include <string>
#include <vector>

#include "profile.h"

namespace detour {

constexpr size_t kMaxApiIpv4 = 24;

struct RoutingPolicy {
  std::string mode;                   // "all" | "exclude" | "include"
  std::vector<std::string> app_paths;  // absolute .exe paths (Windows), max 63
  std::vector<std::string> domains;    // "example.ru" / "*.example.ru", max 128
  // Control-plane addresses that always go direct, 1..kMaxApiIpv4: up to 8 for the primary API
  // origin and up to 8 for each of at most two fallback origins (ADR-195).
  std::vector<std::string> api_ipv4;
  // ADR-106: tunnel DNS chosen by the user. Empty = the server profile DNS (default); otherwise
  // exactly one of the listed resolvers (same list as DnsChoice.java and dns_choice.dart).
  std::string dns_server;
};

// True only for a listed resolver: 1.1.1.1, 8.8.8.8, 94.140.14.14, 9.9.9.9.
bool IsDnsChoice(const std::string& server);

// Lowercase DNS name with at least two labels and an alphabetic TLD (no IP literals).
bool IsLowerDnsName(const std::string& name);

// Absolute drive-letter path to an .exe, without control characters, "..", slashes or ADS colons.
bool IsValidAppPath(const std::string& path);

// Throws std::invalid_argument with one of: routing_policy_invalid, profile_invalid,
// app_invalid, domain_invalid, domain_duplicate.
std::string CreateEngineConfig(const AwgProfile& profile, const RoutingPolicy& policy);

// ADR-112: a panel server (3x-ui / Remnawave) reached by VLESS + Reality. Fields come from the
// API-side parser (services/api/src/vpn_api/panel_sources.py) and are re-checked here. `uuid` is a
// credential: never log, print or persist the generated config.
struct VlessRealityProfile {
  std::string server;       // lowercase DNS name or IPv4 literal
  int port = 0;             // 1..65535
  std::string uuid;         // canonical lowercase 8-4-4-4-12
  std::string flow;         // "" or "xtls-rprx-vision"
  std::string sni;          // lowercase DNS name
  std::string public_key;   // Reality X25519 key, 43 base64url characters
  std::string short_id;     // 0..16 lowercase hex characters, even length
  std::string fingerprint;  // uTLS name, e.g. "chrome"
};

// Same routing policy and error codes as CreateEngineConfig; the tunnel is a `vless` outbound
// tagged "proxy". The default tunnel DNS is 1.1.1.1 (a listed DNS choice) when none is chosen.
// NOT wired to the controller yet and NOT verified against a live server (ADR-112).
std::string CreateVlessEngineConfig(const VlessRealityProfile& profile, const RoutingPolicy& policy);

}  // namespace detour

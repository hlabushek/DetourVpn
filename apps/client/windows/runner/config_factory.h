// Port of apps/client/android/.../LibboxConfigFactory.java for the Windows engine
// (detour-engine.exe, Amnezia Box). Builds the sing-box JSON config from an already validated
// profile and the local routing policy (ADR-079). The result contains the device private key:
// hand it to the engine on stdin and never log, print or persist it.
#pragma once

#include <string>
#include <vector>

#include "profile.h"

namespace detour {

struct RoutingPolicy {
  std::string mode;                   // "all" | "exclude" | "include"
  std::vector<std::string> app_paths;  // absolute .exe paths (Windows), max 63
  std::vector<std::string> domains;    // "example.ru" / "*.example.ru", max 128
  std::vector<std::string> api_ipv4;   // control-plane addresses that always go direct, 1..8
};

// Absolute drive-letter path to an .exe, without control characters, "..", slashes or ADS colons.
bool IsValidAppPath(const std::string& path);

// Throws std::invalid_argument with one of: routing_policy_invalid, profile_invalid,
// app_invalid, domain_invalid, domain_duplicate.
std::string CreateEngineConfig(const AwgProfile& profile, const RoutingPolicy& policy);

}  // namespace detour

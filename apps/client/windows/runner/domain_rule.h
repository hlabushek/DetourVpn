// Port of apps/client/android/.../DomainRule.java (ADR-079). Local-only hostname rule.
// A suffix rule ("*.example.ru") matches the apex and all label-boundary subdomains in the engine.
#pragma once

#include <stdexcept>
#include <string>

namespace detour {

struct DomainRule {
  std::string ascii_host;  // lower-case ASCII / punycode, no trailing dot
  bool suffix = false;

  // "example.ru" or "*.example.ru"; the form used for duplicate detection.
  std::string Canonical() const { return suffix ? "*." + ascii_host : ascii_host; }
};

// Throws std::invalid_argument("domain_invalid") on any malformed input.
DomainRule ParseDomainRule(const std::string& utf8_input);

}  // namespace detour

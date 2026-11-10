// Port of the validation in apps/android Profile.java and ClientProfile.java for the Windows beta.
// The server profile has no PrivateKey; the key is generated locally and injected here. Remote
// app directives, custom routes, IPv6, DNS search domains and hostnames fail closed. Unknown keys
// are rejected rather than ignored.
#pragma once

#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace detour {

struct AwgProfile {
  std::string private_key;                // base64, local only
  std::vector<std::string> addresses;     // IPv4 CIDR strings
  std::vector<std::string> dns_servers;   // IPv4 literals
  int mtu = 1408;                         // Java default when the profile has none
  std::optional<int> listen_port;
  // Integer obfuscation parameters: jc, jmin, jmax, s1..s4.
  std::map<std::string, int> int_params;
  // String parameters: h1..h4, i1..i5, header_protection_key, content_padding_addition,
  // rekey_after_time, rekey_timeout, reject_after_time, keepalive_timeout, max_handshake_attempts.
  std::map<std::string, std::string> string_params;
  std::map<std::string, bool> bool_params;  // random_trailers, disable_cookies

  std::string endpoint_host;  // IPv4 literal only (no DNS bootstrap)
  int endpoint_port = 0;
  std::string peer_public_key;
  std::optional<std::string> preshared_key;
  std::optional<int> persistent_keepalive;
  std::string allowed_ip;  // always "0.0.0.0/0"
};

// `text` is the server profile without PrivateKey; `private_key_b64` is the local device key.
// Throws std::invalid_argument("profile_invalid").
AwgProfile ParseProfile(const std::string& text, const std::string& private_key_b64);

// True for a dotted IPv4 literal with each octet 0..255.
bool IsIpv4Literal(const std::string& value);

}  // namespace detour

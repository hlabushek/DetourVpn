#include "profile.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <set>
#include <sstream>

namespace detour {
namespace {

[[noreturn]] void Invalid() { throw std::invalid_argument("profile_invalid"); }

std::string Trim(const std::string& s) {
  size_t a = 0, b = s.size();
  while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
  while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
  return s.substr(a, b - a);
}

std::string Lower(std::string s) {
  for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

bool AllDigits(const std::string& s, size_t max_len) {
  return !s.empty() && s.size() <= max_len &&
         std::all_of(s.begin(), s.end(), [](unsigned char c) { return std::isdigit(c); });
}

int ToInt(const std::string& s, size_t max_len, int min, int max) {
  if (!AllDigits(s, max_len)) Invalid();
  long v = std::strtol(s.c_str(), nullptr, 10);
  if (v < min || v > max) Invalid();
  return static_cast<int>(v);
}

int Base64Value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// 32-byte key in canonical base64 (44 characters, one '=' pad, zero trailing bits).
bool IsKey(const std::string& s) {
  if (s.size() != 44 || s[43] != '=') return false;
  for (size_t i = 0; i < 43; ++i)
    if (Base64Value(s[i]) < 0) return false;
  return (Base64Value(s[42]) & 3) == 0;
}

bool IsCidr4(const std::string& s) {
  size_t slash = s.find('/');
  if (slash == std::string::npos || !IsIpv4Literal(s.substr(0, slash))) return false;
  std::string prefix = s.substr(slash + 1);
  if (!AllDigits(prefix, 2)) return false;
  int n = std::atoi(prefix.c_str());
  return n >= 1 && n <= 32;
}

std::vector<std::string> SplitList(const std::string& value) {
  std::vector<std::string> out;
  std::stringstream stream(value);
  std::string item;
  while (std::getline(stream, item, ',')) {
    item = Trim(item);
    if (item.empty()) Invalid();
    out.push_back(item);
  }
  if (out.empty()) Invalid();
  return out;
}

// Duration-like AWG tunables: digits with an optional ms/s/m/h unit.
bool IsDuration(const std::string& s) {
  size_t i = 0;
  while (i < s.size() && std::isdigit(static_cast<unsigned char>(s[i]))) ++i;
  if (i == 0 || i > 9) return false;
  std::string unit = s.substr(i);
  return unit.empty() || unit == "ms" || unit == "s" || unit == "m" || unit == "h";
}

// Obfuscation header: a number or a number range ("100-200").
bool IsHeaderValue(const std::string& s) {
  size_t dash = s.find('-');
  if (dash == std::string::npos) return AllDigits(s, 10);
  return AllDigits(s.substr(0, dash), 10) && AllDigits(s.substr(dash + 1), 10);
}

// Special-junk packets (<b 0x..><r n>...): printable ASCII, bounded.
bool IsSpecialJunk(const std::string& s) {
  return !s.empty() && s.size() <= 1024 &&
         std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= 0x20 && c < 0x7f; });
}

}  // namespace

bool IsIpv4Literal(const std::string& value) {
  int groups = 0;
  size_t i = 0;
  while (i <= value.size()) {
    size_t dot = value.find('.', i);
    std::string part = value.substr(i, dot == std::string::npos ? std::string::npos : dot - i);
    if (!AllDigits(part, 3) || std::atoi(part.c_str()) > 255) return false;
    ++groups;
    if (dot == std::string::npos) break;
    i = dot + 1;
  }
  return groups == 4;
}

AwgProfile ParseProfile(const std::string& text, const std::string& private_key_b64) {
  if (text.size() > 16384 || text.find('\0') != std::string::npos || !IsKey(private_key_b64))
    Invalid();

  static const std::set<std::string> kInterfaceInts = {"jc", "jmin", "jmax", "s1", "s2", "s3", "s4"};
  static const std::map<std::string, std::string> kInterfaceStrings = {
      {"h1", "h1"}, {"h2", "h2"}, {"h3", "h3"}, {"h4", "h4"},
      {"i1", "i1"}, {"i2", "i2"}, {"i3", "i3"}, {"i4", "i4"}, {"i5", "i5"},
      {"headerprotectionkey", "header_protection_key"},
      {"contentpaddingaddition", "content_padding_addition"},
      {"rekeyaftertime", "rekey_after_time"}, {"rekeytimeout", "rekey_timeout"},
      {"rejectaftertime", "reject_after_time"}, {"keepalivetimeout", "keepalive_timeout"},
      {"maxhandshakeattempts", "max_handshake_attempts"}};

  AwgProfile profile;
  profile.private_key = private_key_b64;
  enum class Section { kNone, kInterface, kPeer } section = Section::kNone;
  int interface_sections = 0, peer_sections = 0;
  std::set<std::string> seen;  // duplicate keys fail closed, except list keys
  bool have_public = false, have_endpoint = false, have_allowed = false;

  std::stringstream stream(text);
  std::string raw;
  while (std::getline(stream, raw)) {
    std::string line = Trim(raw.substr(0, raw.find('#')));
    if (line.empty()) continue;
    if (line.front() == '[') {
      std::string name = Lower(line);
      if (name == "[interface]") {
        if (++interface_sections > 1 || peer_sections > 0) Invalid();
        section = Section::kInterface;
      } else if (name == "[peer]") {
        if (++peer_sections > 1) Invalid();  // exactly one peer
        section = Section::kPeer;
      } else {
        Invalid();
      }
      seen.clear();
      continue;
    }
    size_t eq = line.find('=');
    if (eq == std::string::npos || section == Section::kNone) Invalid();
    std::string key = Lower(Trim(line.substr(0, eq)));
    std::string value = Trim(line.substr(eq + 1));
    if (key.empty() || value.empty()) Invalid();
    bool list_key = key == "address" || key == "dns";
    if (!list_key && !seen.insert(key).second) Invalid();

    if (section == Section::kInterface) {
      if (key == "privatekey") Invalid();  // the key is local only; a remote one is refused
      if (key == "address") {
        for (const auto& item : SplitList(value)) {
          if (!IsCidr4(item)) Invalid();  // IPv6 and hostnames rejected
          profile.addresses.push_back(item);
        }
      } else if (key == "dns") {
        for (const auto& item : SplitList(value)) {
          if (!IsIpv4Literal(item)) Invalid();  // no hostnames, no search domains, no IPv6
          profile.dns_servers.push_back(item);
        }
      } else if (key == "mtu") {
        profile.mtu = ToInt(value, 4, 576, 1500);
      } else if (key == "listenport") {
        profile.listen_port = ToInt(value, 5, 1, 65535);
      } else if (kInterfaceInts.count(key)) {
        profile.int_params[key] = ToInt(value, 6, 0, 65535);
      } else if (kInterfaceStrings.count(key)) {
        const std::string& name = kInterfaceStrings.at(key);
        bool ok;
        if (name[0] == 'h' && name.size() == 2) ok = IsHeaderValue(value);
        else if (name[0] == 'i' && name.size() == 2) ok = IsSpecialJunk(value);
        else if (name == "header_protection_key") ok = IsKey(value);
        else if (name == "content_padding_addition" || name == "max_handshake_attempts")
          ok = AllDigits(value, 9);
        else ok = IsDuration(value);
        if (!ok) Invalid();
        profile.string_params[name] = value;
      } else if (key == "randomtrailers" || key == "disablecookies") {
        std::string v = Lower(value);
        if (v != "true" && v != "false") Invalid();
        profile.bool_params[key == "randomtrailers" ? "random_trailers" : "disable_cookies"] =
            v == "true";
      } else {
        Invalid();  // IncludedApplications, ExcludedApplications, routes, scripts, anything else
      }
    } else {
      if (key == "publickey") {
        if (!IsKey(value)) Invalid();
        profile.peer_public_key = value;
        have_public = true;
      } else if (key == "presharedkey") {
        if (!IsKey(value)) Invalid();
        profile.preshared_key = value;
      } else if (key == "endpoint") {
        size_t colon = value.rfind(':');
        if (colon == std::string::npos) Invalid();
        profile.endpoint_host = value.substr(0, colon);
        if (!IsIpv4Literal(profile.endpoint_host)) Invalid();  // IPv4 literal only, no DNS bootstrap
        profile.endpoint_port = ToInt(value.substr(colon + 1), 5, 1, 65535);
        have_endpoint = true;
      } else if (key == "allowedips") {
        if (value != "0.0.0.0/0") Invalid();  // full IPv4 route only; no custom routes, no ::/0
        profile.allowed_ip = value;
        have_allowed = true;
      } else if (key == "persistentkeepalive") {
        profile.persistent_keepalive = ToInt(value, 5, 0, 65535);
      } else {
        Invalid();
      }
    }
  }

  if (interface_sections != 1 || peer_sections != 1 || profile.addresses.empty() ||
      profile.dns_servers.empty() || !have_public || !have_endpoint || !have_allowed)
    Invalid();
  return profile;
}

}  // namespace detour

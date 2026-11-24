#include "config_factory.h"

#include <algorithm>
#include <cctype>
#include <set>

#include "domain_rule.h"
#include "json_writer.h"

namespace detour {
namespace {

[[noreturn]] void Fail(const char* code) { throw std::invalid_argument(code); }

std::string Lower(std::string s) {
  for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

// Absolute drive-letter path to an .exe: no control characters, quotes, wildcards, "..", slashes,
// ADS colons or relative parts. Windows paths are case-insensitive; matching is made so below.
bool ValidAppPath(const std::string& p) {
  if (p.size() < 7 || p.size() > 520) return false;
  if (!std::isalpha(static_cast<unsigned char>(p[0])) || p[1] != ':' || p[2] != '\\') return false;
  for (size_t i = 0; i < p.size(); ++i) {
    unsigned char c = static_cast<unsigned char>(p[i]);
    if (c < 0x20 || c == 0x7f || c == '"' || c == '<' || c == '>' || c == '|' || c == '*' ||
        c == '?' || c == '/')
      return false;
    if (c == ':' && i != 1) return false;
  }
  std::string lower = Lower(p);
  if (lower.compare(lower.size() - 4, 4, ".exe") != 0) return false;
  // Every component must be non-empty and not "." / "..", and must not end in a dot or space.
  size_t start = 3;
  while (start <= p.size()) {
    size_t slash = p.find('\\', start);
    std::string part = p.substr(start, slash == std::string::npos ? std::string::npos : slash - start);
    if (part.empty() || part == "." || part == ".." || part.back() == '.' || part.back() == ' ')
      return false;
    if (slash == std::string::npos) break;
    start = slash + 1;
  }
  return true;
}

// Case-insensitive exact match of one executable path, as a Go (RE2) expression.
std::string PathRegex(const std::string& path) {
  std::string out = "(?i)^";
  for (char c : path) {
    if (std::string("\\.+*?()|[]{}^$").find(c) != std::string::npos) out.push_back('\\');
    out.push_back(c);
  }
  out += "$";
  return out;
}

Json StringArray(const std::vector<std::string>& values) {
  Json a = Json::MakeArray();
  for (const auto& v : values) a.Add(v);
  return a;
}

bool IsDnsChoiceValue(const std::string& server) {
  return server == "1.1.1.1" || server == "8.8.8.8" || server == "94.140.14.14" || server == "9.9.9.9";
}

void ValidatePolicy(const RoutingPolicy& policy) {
  const std::string& mode = policy.mode;
  if ((mode != "all" && mode != "exclude" && mode != "include") || policy.app_paths.size() > 63 ||
      policy.domains.size() > 128 || policy.api_ipv4.empty() || policy.api_ipv4.size() > kMaxApiIpv4 ||
      (mode == "include" && policy.app_paths.empty() && policy.domains.empty()))
    Fail("routing_policy_invalid");
  if (std::set<std::string>(policy.domains.begin(), policy.domains.end()).size() !=
      policy.domains.size())
    Fail("routing_policy_invalid");
  for (const auto& ip : policy.api_ipv4)
    if (!IsIpv4Literal(ip)) Fail("routing_policy_invalid");
  if (!policy.dns_server.empty() && !IsDnsChoiceValue(policy.dns_server))
    Fail("routing_policy_invalid");
}

// The tunnel-specific parts of a config; everything else is shared by both engines.
struct TunnelSpec {
  std::string tag;                         // route/DNS target: "awg" (endpoint) or "proxy"
  std::vector<std::string> tun_addresses;  // TUN inbound addresses before the IPv6 ULA
  int mtu = 0;
  std::string profile_dns;  // tunnel DNS server when the user chose none
  // VLESS: the panel host name must resolve outside the tunnel, so a system resolver always
  // exists (a tunnel DNS server would need the tunnel to reach the tunnel).
  bool always_local_dns = false;
};

std::string BuildConfig(const RoutingPolicy& policy, const TunnelSpec& spec, Json tunnel,
                        bool tunnel_is_endpoint) {
  const std::string& mode = policy.mode;
  // TUN: a local ULA address makes Windows route ::/0 into the TUN, where IPv6 is rejected below.
  std::vector<std::string> tun_addresses = spec.tun_addresses;
  tun_addresses.push_back(std::string(kTunIpv6Ula) + "/126");
  Json tun;
  tun.Put("type", "tun").Put("tag", "tun-in").Put("address", StringArray(tun_addresses));
  tun.Put("mtu", spec.mtu).Put("auto_route", true).Put("strict_route", true);
  tun.Put("stack", "gvisor");
  tun.Put("route_address", StringArray({"0.0.0.0/0", "::/0"}));

  // Selection rules, shared by route and DNS rules.
  const bool split = mode != "all";
  std::vector<std::string> regexes, exact, suffixes;
  if (split) {
    std::set<std::string> seen_paths;
    for (const auto& path : policy.app_paths) {
      if (!ValidAppPath(path) || !seen_paths.insert(Lower(path)).second) Fail("app_invalid");
      regexes.push_back(PathRegex(path));
    }
    std::set<std::string> canonical;
    for (const auto& raw : policy.domains) {
      DomainRule rule = ParseDomainRule(raw);
      if (!canonical.insert(rule.Canonical()).second) Fail("domain_duplicate");
      (rule.suffix ? suffixes : exact).push_back(rule.ascii_host);
    }
  }
  const std::string target = mode == "exclude" ? "direct" : spec.tag;

  auto selection = [&](const std::string& outbound_key, const std::string& outbound_value) {
    std::vector<Json> rules;
    if (!regexes.empty())
      rules.push_back(Json().Put("process_path_regex", StringArray(regexes))
                          .Put(outbound_key, outbound_value));
    if (!exact.empty())
      rules.push_back(Json().Put("domain", StringArray(exact)).Put(outbound_key, outbound_value));
    if (!suffixes.empty())
      rules.push_back(
          Json().Put("domain_suffix", StringArray(suffixes)).Put(outbound_key, outbound_value));
    return rules;
  };

  // Route rules in order: sniff, hijack-dns, IPv6 reject, API bypass, selections, final.
  Json rules = Json::MakeArray();
  rules.Add(Json().Put("action", "sniff"));
  rules.Add(Json().Put("protocol", "dns").Put("action", "hijack-dns"));
  rules.Add(Json().Put("ip_version", 6).Put("action", "reject"));
  Json api_cidrs = Json::MakeArray();
  for (const auto& ip : policy.api_ipv4) api_cidrs.Add(ip + "/32");
  rules.Add(Json().Put("ip_cidr", std::move(api_cidrs)).Put("outbound", "direct"));
  if (split)
    for (auto& rule : selection("outbound", target)) rules.Add(std::move(rule));

  // DNS: the profile DNS through the tunnel, and the system resolver for names that go direct.
  const std::string selected_server = mode == "exclude" ? "local-dns" : "profile-dns";
  Json dns_rules = Json::MakeArray();
  if (split)
    for (auto& rule : selection("server", selected_server)) dns_rules.Add(std::move(rule));
  Json dns_servers = Json::MakeArray();
  dns_servers.Add(Json().Put("type", "udp").Put("tag", "profile-dns")
                      .Put("server", policy.dns_server.empty() ? spec.profile_dns
                                                                : policy.dns_server)
                      .Put("detour", spec.tag));
  // The system resolver exists only when some names can go direct; with split off the tunnel DNS is
  // the single resolver (and the engine's "two transports need a default resolver" rule is moot).
  const bool local_dns = split || spec.always_local_dns;
  if (local_dns) dns_servers.Add(Json().Put("type", "local").Put("tag", "local-dns"));
  Json dns;
  dns.Put("servers", std::move(dns_servers)).Put("rules", std::move(dns_rules));
  dns.Put("final", mode == "include" ? "local-dns" : "profile-dns").Put("strategy", "ipv4_only");

  Json route;
  route.Put("rules", std::move(rules)).Put("final", mode == "include" ? "direct" : spec.tag);
  route.Put("find_process", !regexes.empty()).Put("auto_detect_interface", true);
  // Two DNS transports require an explicit default resolver for dial fields; use the same server as
  // the DNS final so a name is never resolved on a path its rule did not choose.
  if (local_dns)
    route.Put("default_domain_resolver", mode == "include" ? "local-dns" : "profile-dns");

  Json outbounds = Json::MakeArray();
  outbounds.Add(Json().Put("type", "direct").Put("tag", "direct"));
  if (!tunnel_is_endpoint) outbounds.Add(std::move(tunnel));

  Json root;
  root.Put("log", Json().Put("level", "error")).Put("dns", std::move(dns));
  root.Put("inbounds", Json::MakeArray().Add(std::move(tun)));
  if (tunnel_is_endpoint) root.Put("endpoints", Json::MakeArray().Add(std::move(tunnel)));
  root.Put("outbounds", std::move(outbounds)).Put("route", std::move(route));
  return root.Dump();
}

// Lowercase DNS name with at least two labels and an alphabetic TLD (no IP literals).
bool IsDnsName(const std::string& name) {
  if (name.empty() || name.size() > 253) return false;
  size_t start = 0, labels = 0;
  while (true) {
    size_t dot = name.find('.', start);
    std::string label = name.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
    if (label.empty() || label.size() > 63 || label.front() == '-' || label.back() == '-')
      return false;
    for (char c : label)
      if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-')) return false;
    ++labels;
    if (dot == std::string::npos) return labels >= 2 && label.front() >= 'a' && label.front() <= 'z';
    start = dot + 1;
  }
}

bool IsCanonicalUuid(const std::string& uuid) {
  if (uuid.size() != 36) return false;
  for (size_t i = 0; i < uuid.size(); ++i) {
    const char c = uuid[i];
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (c != '-') return false;
    } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
      return false;
    }
  }
  return true;
}

bool IsRealityPublicKey(const std::string& key) {
  if (key.size() != 43) return false;
  for (char c : key)
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' ||
          c == '_'))
      return false;
  return true;
}

bool IsShortId(const std::string& id) {
  if (id.size() > 16 || id.size() % 2 != 0) return false;
  for (char c : id)
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}

bool IsFingerprint(const std::string& fp) {
  static const std::set<std::string> kNames = {"chrome", "firefox", "edge",    "safari", "360",
                                               "qq",     "ios",     "android", "random", "randomized"};
  return kNames.count(fp) == 1;
}

}  // namespace

bool IsValidAppPath(const std::string& path) { return ValidAppPath(path); }

bool IsDnsChoice(const std::string& server) { return IsDnsChoiceValue(server); }

bool IsLowerDnsName(const std::string& name) { return IsDnsName(name); }

std::string CreateEngineConfig(const AwgProfile& profile, const RoutingPolicy& policy) {
  ValidatePolicy(policy);
  if (profile.addresses.empty() || profile.dns_servers.empty() || profile.endpoint_host.empty() ||
      profile.peer_public_key.empty() || profile.private_key.empty())
    Fail("profile_invalid");

  // Endpoint (type "awg"): same field mapping as the Java factory.
  Json awg;
  awg.Put("type", "awg").Put("tag", "awg").Put("useIntegratedTun", false);
  awg.Put("private_key", profile.private_key);
  awg.Put("address", StringArray(profile.addresses));
  awg.Put("mtu", profile.mtu);
  if (profile.listen_port) awg.Put("listen_port", *profile.listen_port);
  for (const auto& [name, value] : profile.int_params) awg.Put(name, value);
  for (const auto& [name, value] : profile.string_params) awg.Put(name, value);
  for (const auto& [name, value] : profile.bool_params) awg.Put(name, value);

  Json peer;
  peer.Put("address", profile.endpoint_host).Put("port", profile.endpoint_port);
  peer.Put("public_key", profile.peer_public_key);
  if (profile.preshared_key) peer.Put("preshared_key", *profile.preshared_key);
  if (profile.persistent_keepalive)
    peer.Put("persistent_keepalive_interval", *profile.persistent_keepalive);
  peer.Put("allowed_ips", StringArray({profile.allowed_ip}));
  awg.Put("peers", Json::MakeArray().Add(std::move(peer)));

  TunnelSpec spec;
  spec.tag = "awg";
  spec.tun_addresses = profile.addresses;
  spec.mtu = profile.mtu;
  spec.profile_dns = profile.dns_servers.front();
  return BuildConfig(policy, spec, std::move(awg), /*tunnel_is_endpoint=*/true);
}

std::string CreateVlessEngineConfig(const VlessRealityProfile& profile, const RoutingPolicy& policy) {
  ValidatePolicy(policy);
  if (!(IsIpv4Literal(profile.server) || IsDnsName(profile.server)) || profile.port < 1 ||
      profile.port > 65535 || !IsCanonicalUuid(profile.uuid) ||
      (profile.flow != "" && profile.flow != "xtls-rprx-vision") || !IsDnsName(profile.sni) ||
      !IsRealityPublicKey(profile.public_key) || !IsShortId(profile.short_id) ||
      !IsFingerprint(profile.fingerprint))
    Fail("profile_invalid");

  Json tls;
  tls.Put("enabled", true).Put("server_name", profile.sni);
  tls.Put("utls", Json().Put("enabled", true).Put("fingerprint", profile.fingerprint));
  tls.Put("reality", Json().Put("enabled", true).Put("public_key", profile.public_key)
                         .Put("short_id", profile.short_id));
  Json vless;
  vless.Put("type", "vless").Put("tag", "proxy");
  vless.Put("server", profile.server).Put("server_port", profile.port);
  vless.Put("uuid", profile.uuid);
  if (!profile.flow.empty()) vless.Put("flow", profile.flow);
  vless.Put("packet_encoding", "xudp");
  // The panel host is resolved by the system resolver, never through this outbound itself.
  vless.Put("domain_resolver", "local-dns");
  vless.Put("tls", std::move(tls));

  TunnelSpec spec;
  spec.tag = "proxy";
  // VLESS carries no tunnel address; a private /30 is the TUN's own address only.
  spec.tun_addresses = {"172.19.0.1/30"};
  spec.mtu = 1500;
  spec.profile_dns = "1.1.1.1";
  spec.always_local_dns = true;
  return BuildConfig(policy, spec, std::move(vless), /*tunnel_is_endpoint=*/false);
}

}  // namespace detour

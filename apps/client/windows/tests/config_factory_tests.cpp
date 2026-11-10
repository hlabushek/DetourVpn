// Unit tests for domain_rule, profile and config_factory (I-032 stage 1).
// The Java classes have no test vectors of their own, so the vectors below are derived from the
// Java behaviour and ADR-079; ClientProfileTest.java cases are mirrored in the profile tests.
// All keys are obviously fake test values.
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

#include "config_factory.h"
#include "domain_rule.h"
#include "json_writer.h"
#include "profile.h"

namespace {

int g_failures = 0;
int g_checks = 0;

void Check(bool ok, const char* what, int line) {
  ++g_checks;
  if (!ok) {
    ++g_failures;
    std::printf("FAIL line %d: %s\n", line, what);
  }
}
// No stringizing of the condition: MSVC mishandles raw string literals inside #cond.
#define CHECK(cond) Check((cond), "check failed", __LINE__)

// True when f() throws std::invalid_argument with exactly `code`.
bool Throws(const char* code, const std::function<void()>& f) {
  try {
    f();
  } catch (const std::invalid_argument& e) {
    return std::string(e.what()) == code;
  }
  return false;
}

bool Has(const std::string& haystack, const std::string& needle) {
  return haystack.find(needle) != std::string::npos;
}
size_t Pos(const std::string& haystack, const std::string& needle) { return haystack.find(needle); }

std::string Key(char fill) { return std::string(42, fill) + "A="; }

std::string ProfileText() {
  return "[Interface]\nAddress = 10.203.71.2/30\nDNS = 192.0.2.53\nMTU = 1280\n"
         "Jc = 4\nJmin = 64\nJmax = 96\nS1 = 16\nS2 = 24\nS3 = 32\nS4 = 40\n"
         "H1 = 101234\nH2 = 202345\nH3 = 303456\nH4 = 404567\n"
         "HeaderProtectionKey = " + Key('h') + "\n[Peer]\nPublicKey = " + Key('p') +
         "\nEndpoint = 192.0.2.1:51901\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n";
}

std::string Replace(std::string text, const std::string& from, const std::string& to) {
  size_t at = text.find(from);
  if (at != std::string::npos) text.replace(at, from.size(), to);
  return text;
}

detour::AwgProfile Parse() { return detour::ParseProfile(ProfileText(), Key('k')); }

detour::RoutingPolicy Policy(const std::string& mode) {
  detour::RoutingPolicy p;
  p.mode = mode;
  p.api_ipv4 = {"198.51.100.7"};
  return p;
}

std::string Make(const detour::RoutingPolicy& p) { return detour::CreateEngineConfig(Parse(), p); }

// ---- DomainRule ----------------------------------------------------------------------------

void DomainValid() {
  auto r = detour::ParseDomainRule("Example.RU");
  CHECK(r.ascii_host == "example.ru" && !r.suffix && r.Canonical() == "example.ru");
  r = detour::ParseDomainRule("*.Example.ru");
  CHECK(r.ascii_host == "example.ru" && r.suffix && r.Canonical() == "*.example.ru");
  CHECK(detour::ParseDomainRule("example.ru.").ascii_host == "example.ru");   // one trailing dot
  CHECK(detour::ParseDomainRule("*.example.ru.").Canonical() == "*.example.ru");
  CHECK(detour::ParseDomainRule("  example.ru \t").ascii_host == "example.ru");  // trim
  CHECK(detour::ParseDomainRule("sub.example.co.uk").ascii_host == "sub.example.co.uk");
  CHECK(detour::ParseDomainRule("a-b.example.ru").ascii_host == "a-b.example.ru");
  CHECK(detour::ParseDomainRule("*.ru").Canonical() == "*.ru");  // a bare TLD is allowed as suffix
  CHECK(detour::ParseDomainRule("xn--e1afmkfd.xn--p1ai").ascii_host == "xn--e1afmkfd.xn--p1ai");
  // IDNA (UTF-8 source strings; the test is compiled with /utf-8)
  CHECK(detour::ParseDomainRule("пример.рф").ascii_host == "xn--e1afmkfd.xn--p1ai");
  CHECK(detour::ParseDomainRule("*.ПРИМЕР.рф").Canonical() == "*.xn--e1afmkfd.xn--p1ai");
  CHECK(detour::ParseDomainRule("bücher.de").ascii_host == "xn--bcher-kva.de");
  // 63-char label and 253-char name are the limits
  CHECK(detour::ParseDomainRule(std::string(63, 'a') + ".ru").ascii_host.size() == 66);
}

void DomainInvalid() {
  auto bad = [](const std::string& s) {
    return Throws("domain_invalid", [&] { detour::ParseDomainRule(s); });
  };
  CHECK(bad(""));
  CHECK(bad("   "));
  CHECK(bad("localhost"));                 // exact rule needs a dot
  CHECK(bad("1.2.3.4"));                   // IP literal
  CHECK(bad("1.2.3.4."));                  // IP literal with a trailing dot
  CHECK(bad("*.1.2.3.4"));
  CHECK(bad("999.1.1.1"));                 // Java regex rejects any 1-3 digit quad
  CHECK(bad("https://example.ru"));
  CHECK(bad("example.ru/path"));
  CHECK(bad("example.ru?x=1"));
  CHECK(bad("example.ru#f"));
  CHECK(bad("user@example.ru"));
  CHECK(bad("example.ru:443"));
  CHECK(bad("back\\slash.ru"));
  CHECK(bad("ex*ample.ru"));
  CHECK(bad("*example.ru"));
  CHECK(bad("**.example.ru"));
  CHECK(bad("*."));
  CHECK(bad("."));
  CHECK(bad(".."));
  CHECK(bad("a..b.ru"));
  CHECK(bad("example.ru.."));              // only one trailing dot is removed
  CHECK(bad(".example.ru"));
  CHECK(bad("-bad.ru"));
  CHECK(bad("bad-.ru"));
  CHECK(bad("under_score.ru"));            // STD3 rules
  CHECK(bad("ex ample.ru"));
  CHECK(bad(std::string(64, 'a') + ".ru")); // label too long
  CHECK(bad(std::string(257, 'a')));        // input too long
  std::string long_name;
  for (int i = 0; i < 26; ++i) long_name += std::string(9, 'a') + ".";
  CHECK(bad(long_name + "ru"));            // > 253 characters
  CHECK(bad(std::string("bad\xff.ru")));   // invalid UTF-8
}

// ---- Profile -------------------------------------------------------------------------------

void ProfileValid() {
  auto p = Parse();
  CHECK(p.addresses.size() == 1 && p.addresses[0] == "10.203.71.2/30");
  CHECK(p.dns_servers.size() == 1 && p.dns_servers[0] == "192.0.2.53");
  CHECK(p.mtu == 1280 && p.int_params.at("jc") == 4 && p.int_params.at("s4") == 40);
  CHECK(p.string_params.at("h1") == "101234" && p.string_params.at("header_protection_key") == Key('h'));
  CHECK(p.endpoint_host == "192.0.2.1" && p.endpoint_port == 51901);
  CHECK(p.persistent_keepalive && *p.persistent_keepalive == 25);
  CHECK(p.private_key == Key('k'));
  // comments and CRLF are tolerated
  std::string noisy;
  for (char c : ProfileText()) noisy += (c == '\n') ? std::string(" # note\r\n") : std::string(1, c);
  CHECK(detour::ParseProfile(noisy, Key('k')).endpoint_port == 51901);
}

void ProfileInvalid() {
  auto bad = [](const std::string& text, const std::string& key = Key('k')) {
    return Throws("profile_invalid", [&] { detour::ParseProfile(text, key); });
  };
  CHECK(!bad(ProfileText()));
  CHECK(bad(ProfileText(), "short"));                                   // bad local key
  CHECK(bad(ProfileText() + std::string(20000, '#')));                  // too large
  CHECK(bad(std::string(ProfileText()).replace(5, 1, std::string(1, '\0'))));  // NUL
  // Remote key, remote app directives, custom routes: fail closed
  CHECK(bad(Replace(ProfileText(), "DNS =", "PrivateKey = " + Key('x') + "\nDNS =")));
  CHECK(bad(Replace(ProfileText(), "DNS =", "ExcludedApplications = org.bad.app\nDNS =")));
  CHECK(bad(Replace(ProfileText(), "DNS =", "IncludedApplications = org.bad.app\nDNS =")));
  CHECK(bad(Replace(ProfileText(), "DNS =", "PostUp = calc.exe\nDNS =")));
  CHECK(bad(Replace(ProfileText(), "0.0.0.0/0", "0.0.0.0/0, ::/0")));   // IPv6 passthrough
  CHECK(bad(Replace(ProfileText(), "0.0.0.0/0", "10.0.0.0/8")));        // custom route
  CHECK(bad(Replace(ProfileText(), "192.0.2.1:51901", "node.example:51901")));  // hostname
  CHECK(bad(Replace(ProfileText(), "192.0.2.1:51901", "[2001:db8::1]:51901")));
  CHECK(bad(Replace(ProfileText(), "192.0.2.1:51901", "192.0.2.1:0")));
  CHECK(bad(Replace(ProfileText(), "DNS = 192.0.2.53", "DNS = dns.example")));   // DNS hostname
  CHECK(bad(Replace(ProfileText(), "DNS = 192.0.2.53", "DNS = 2001:db8::53")));  // DNS IPv6
  CHECK(bad(Replace(ProfileText(), "DNS = 192.0.2.53\n", "")));                  // no DNS
  CHECK(bad(Replace(ProfileText(), "Address = 10.203.71.2/30", "Address = fd00::2/64")));
  CHECK(bad(Replace(ProfileText(), "MTU = 1280", "MTU = 70000")));
  CHECK(bad(Replace(ProfileText(), "Jc = 4", "Jc = x")));
  CHECK(bad(ProfileText() + "[Peer]\nPublicKey = " + Key('q') + "\n"));   // two peers
  CHECK(bad("[Interface]\n" + ProfileText()));                             // two interfaces
  CHECK(bad(Replace(ProfileText(), "Jc = 4\n", "Jc = 4\nJc = 5\n")));      // duplicate key
  CHECK(bad(Replace(ProfileText(), "PublicKey = " + Key('p'), "PublicKey = notakey")));
  CHECK(bad(Replace(ProfileText(), "[Peer]", "[Peer]\nUnknownKey = 1")));
}

// ---- Config factory ------------------------------------------------------------------------

void ConfigAllMode() {
  std::string c = Make(Policy("all"));
  CHECK(Has(c, R"("type":"awg")") && Has(c, R"("useIntegratedTun":false)"));
  CHECK(Has(c, R"("private_key":")" + Key('k') + "\""));
  CHECK(Has(c, R"("address":"192.0.2.1","port":51901)"));
  CHECK(Has(c, R"("allowed_ips":["0.0.0.0/0"])") && Has(c, R"("persistent_keepalive_interval":25)"));
  CHECK(Has(c, R"("jc":4)") && Has(c, R"("h1":"101234")"));
  CHECK(Has(c, R"("auto_route":true,"strict_route":true)") && Has(c, R"("stack":"gvisor")"));
  CHECK(Has(c, R"("route_address":["0.0.0.0/0","::/0"])") && Has(c, "fdfe:dcba:9876::1/126"));
  CHECK(Has(c, R"("final":"awg")") && Has(c, R"("find_process":false)"));
  CHECK(Has(c, R"("strategy":"ipv4_only")") && Has(c, R"("server":"192.0.2.53","detour":"awg")"));
  CHECK(!Has(c, "domain_suffix") && !Has(c, "process_path_regex"));  // split off: no selections
  CHECK(!Has(c, "local-dns") && !Has(c, "default_domain_resolver"));  // single resolver
  // rule order: sniff, hijack-dns, IPv6 reject, API bypass
  size_t sniff = Pos(c, R"({"action":"sniff"})");
  size_t hijack = Pos(c, R"("action":"hijack-dns")");
  size_t v6 = Pos(c, R"("ip_version":6,"action":"reject")");
  size_t api = Pos(c, R"("ip_cidr":["198.51.100.7/32"],"outbound":"direct")");
  CHECK(sniff != std::string::npos && sniff < hijack && hijack < v6 && v6 < api);
  CHECK(!Has(c, "reverse_mapping") && !Has(c, "fakeip"));  // ADR-079: no guessing from IPs
}

void ConfigExcludeAndInclude() {
  auto policy = Policy("exclude");
  policy.domains = {"*.Example.ru", "exact.example.net"};
  policy.app_paths = {"C:\\Program Files\\App\\app.exe"};
  std::string ex = Make(policy);
  CHECK(Has(ex, R"("domain":["exact.example.net"],"outbound":"direct")"));
  CHECK(Has(ex, R"("domain_suffix":["example.ru"],"outbound":"direct")"));  // not "*.example.ru"
  CHECK(Has(ex, R"("process_path_regex":["(?i)^C:\\\\Program Files\\\\App\\\\app\\.exe$"],"outbound":"direct")"));
  CHECK(Has(ex, R"("final":"awg")") && Has(ex, R"("find_process":true)"));
  CHECK(Has(ex, R"("server":"local-dns")") && Has(ex, R"("final":"profile-dns")"));
  CHECK(Has(ex, R"("default_domain_resolver":"profile-dns")"));
  // selections come after the API bypass
  CHECK(Pos(ex, "198.51.100.7/32") < Pos(ex, R"("domain":["exact.example.net"],"outbound")"));

  policy.mode = "include";
  std::string in = Make(policy);
  CHECK(Has(in, R"("domain_suffix":["example.ru"],"outbound":"awg")"));
  CHECK(Has(in, R"("final":"direct")"));
  CHECK(Has(in, R"("server":"profile-dns")") && Has(in, R"("final":"local-dns")"));
  CHECK(Has(in, R"("default_domain_resolver":"local-dns")"));
  CHECK(Has(in, "198.51.100.7/32"));  // API bypass stays in include mode too
}

void ConfigRejections() {
  auto bad = [](const char* code, const detour::RoutingPolicy& p) {
    return Throws(code, [&] { Make(p); });
  };
  auto p = Policy("bogus");
  CHECK(bad("routing_policy_invalid", p));
  p = Policy("include");                                           // empty include = fail-open risk
  CHECK(bad("routing_policy_invalid", p));
  p = Policy("exclude");
  p.api_ipv4.clear();
  CHECK(bad("routing_policy_invalid", p));                         // API bypass is mandatory
  p = Policy("exclude");
  p.api_ipv4 = {"example.org"};
  CHECK(bad("routing_policy_invalid", p));
  p = Policy("exclude");
  for (int i = 0; i < 64; ++i) p.app_paths.push_back("C:\\a\\app" + std::to_string(i) + ".exe");
  CHECK(bad("routing_policy_invalid", p));                         // > 63 apps
  p = Policy("exclude");
  for (int i = 0; i < 129; ++i) p.domains.push_back("h" + std::to_string(i) + ".example.ru");
  CHECK(bad("routing_policy_invalid", p));                         // > 128 domains
  p = Policy("exclude");
  p.domains = {"example.ru", "example.ru"};
  CHECK(bad("routing_policy_invalid", p));                         // identical strings
  p.domains = {"Example.ru", "example.ru."};
  CHECK(bad("domain_duplicate", p));                               // same canonical form
  p.domains = {"https://example.ru"};
  CHECK(bad("domain_invalid", p));
  p = Policy("exclude");
  for (const char* app : {"app.exe", "C:\\a\\..\\b.exe", "C:/a/b.exe", "C:\\a\\b.txt", "\\\\srv\\s\\a.exe",
                          "C:\\a\\\\b.exe", "C:\\a\\b.exe\"", "C:\\a\\b*.exe", "C:\\a:ads\\b.exe"}) {
    p.app_paths = {app};
    CHECK(bad("app_invalid", p));
  }
  p.app_paths = {"C:\\a\\b.exe", "c:\\A\\B.EXE"};
  CHECK(bad("app_invalid", p));                                    // duplicate, case-insensitive
  // split off ('all') ignores remembered selections instead of failing, as in Java
  p = Policy("all");
  p.domains = {"example.ru"};
  CHECK(!Has(Make(p), "example.ru"));
}

void JsonEscaping() {
  detour::Json j;
  j.Put("k", std::string("a\"b\\c\n\x01"));
  CHECK(j.Dump() == "{\"k\":\"a\\\"b\\\\c\\n\\u0001\"}");
}

}  // namespace

int main() {
  DomainValid();
  DomainInvalid();
  ProfileValid();
  ProfileInvalid();
  ConfigAllMode();
  ConfigExcludeAndInclude();
  ConfigRejections();
  JsonEscaping();
  std::printf("%d checks, %d failures\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}

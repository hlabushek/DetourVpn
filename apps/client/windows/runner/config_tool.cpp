// Stage 1 gate helper (I-032): turns a server profile + local key into the engine config on stdout,
// to be piped into `detour-engine.exe run -c stdin`. Not shipped in the release zip.
// It prints only fixed error codes; never the profile, key or config content to stderr.
#include <fcntl.h>
#include <io.h>

#include <cstdio>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

#include "config_factory.h"
#include "profile.h"

namespace {

bool ReadFile(const std::string& path, std::string* out) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return false;
  out->assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
  return in.good() || in.eof();
}

std::string TrimKey(std::string s) {
  while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
  return s;
}

int Usage() {
  std::fputs(
      "usage: detour_config_tool --profile FILE --key-file FILE --api-ip IPV4 [--api-ip IPV4]...\n"
      "       [--mode all|exclude|include] [--app C:\\path\\app.exe]... [--domain NAME]...\n"
      "       detour_config_tool --vless-selftest\n",
      stderr);
  return 64;
}

}  // namespace

// ADR-112 engine self-test: the real VLESS + Reality generator on a fixed, obviously fake profile
// (reserved test names, documentation API address, zero UUID). Pipe it into
// `detour-engine.exe check -c stdin`; an engine built without `with_utls` refuses it.
int VlessSelfTest() {
  detour::VlessRealityProfile v;
  v.server = "panel.example.test";
  v.port = 443;
  v.uuid = "00000000-0000-4000-8000-000000000001";
  v.flow = "xtls-rprx-vision";
  v.sni = "www.example.test";
  v.public_key = std::string(42, 'r') + "A";
  v.short_id = "0a1b";
  v.fingerprint = "chrome";
  detour::RoutingPolicy policy;
  policy.mode = "all";
  policy.api_ipv4 = {"198.51.100.7"};
  try {
    std::string config = detour::CreateVlessEngineConfig(v, policy);
    _setmode(_fileno(stdout), _O_BINARY);
    std::fwrite(config.data(), 1, config.size(), stdout);
    std::fflush(stdout);
  } catch (const std::invalid_argument& failure) {
    std::fprintf(stderr, "error=%s\n", failure.what());
    return 2;
  }
  return 0;
}

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--vless-selftest") return VlessSelfTest();
  std::string profile_path, key_path;
  detour::RoutingPolicy policy;
  policy.mode = "all";
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (i + 1 >= argc) return Usage();
    std::string value = argv[++i];
    if (arg == "--profile") profile_path = value;
    else if (arg == "--key-file") key_path = value;
    else if (arg == "--mode") policy.mode = value;
    else if (arg == "--app") policy.app_paths.push_back(value);
    else if (arg == "--domain") policy.domains.push_back(value);
    else if (arg == "--api-ip") policy.api_ipv4.push_back(value);
    else return Usage();
  }
  if (profile_path.empty() || key_path.empty()) return Usage();

  std::string profile_text, key_text;
  if (!ReadFile(profile_path, &profile_text) || !ReadFile(key_path, &key_text)) {
    std::fputs("error=input_unreadable\n", stderr);
    return 2;
  }
  try {
    auto profile = detour::ParseProfile(profile_text, TrimKey(key_text));
    std::string config = detour::CreateEngineConfig(profile, policy);
    _setmode(_fileno(stdout), _O_BINARY);
    std::fwrite(config.data(), 1, config.size(), stdout);
    std::fflush(stdout);
  } catch (const std::invalid_argument& failure) {
    std::fprintf(stderr, "error=%s\n", failure.what());
    return 2;
  }
  return 0;
}

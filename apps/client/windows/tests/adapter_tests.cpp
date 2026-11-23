// Unit tests for the Windows native adapter (I-032 stage 3): key confirmation (RFC 7748 / RFC 4231
// vectors), DPAPI vault, window fencing (vectors from WindowDeadlineTest.java), engine process
// handling and the controller state machine. The engine is a TEST DOUBLE (fake_engine.exe): these
// tests prove adapter logic only, not a tunnel handshake, egress or routing.
#include "win_headers.h"

#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <functional>
#include <string>
#include <thread>

#include "engine_process.h"
#include "key_confirmation.h"
#include "secure_vault.h"
#include "tunnel_controller.h"
#include "window_deadline.h"

namespace fs = std::filesystem;
using namespace detour;

namespace {

int g_failures = 0, g_checks = 0;
void Check(bool ok, int line) {
  ++g_checks;
  if (!ok) {
    ++g_failures;
    std::printf("FAIL line %d\n", line);
  }
}
#define CHECK(cond) Check((cond), __LINE__)

template <typename Ex = std::exception>
bool Throws(const std::function<void()>& f, const char* what = nullptr) {
  try {
    f();
  } catch (const Ex& e) {
    return what == nullptr || std::string(e.what()) == what;
  }
  return false;
}
bool ThrowsCode(const char* code, const std::function<void()>& f) {
  try {
    f();
  } catch (const NativeError& e) {
    return e.code == code;
  }
  return false;
}

std::vector<uint8_t> FromHex(const std::string& hex) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < hex.size(); i += 2) out.push_back(static_cast<uint8_t>(std::stoi(hex.substr(i, 2), nullptr, 16)));
  return out;
}
std::string B64FromHex(const std::string& hex) {
  auto bytes = FromHex(hex);
  return Base64Encode(bytes.data(), bytes.size());
}
std::string ToHex(const std::vector<uint8_t>& v) {
  static const char* d = "0123456789abcdef";
  std::string s;
  for (uint8_t b : v) {
    s.push_back(d[b >> 4]);
    s.push_back(d[b & 15]);
  }
  return s;
}

fs::path g_tests_dir;  // where fake_engine.exe lives

fs::path FreshDir(const char* name) {
  fs::path dir = fs::temp_directory_path() / (std::string("detour-adapter-") + name);
  std::error_code ec;
  fs::remove_all(dir, ec);
  fs::create_directories(dir);
  return dir;
}

// ---- key confirmation ----------------------------------------------------------------------------

void KeyTests() {
  const std::string alice_priv = "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a";
  const std::string alice_pub = "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a";
  const std::string bob_priv = "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb";
  const std::string bob_pub = "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f";
  const std::string shared = "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742";
  // RFC 7748 section 6.1
  CHECK(PublicKeyOf(B64FromHex(alice_priv)) == B64FromHex(alice_pub));
  CHECK(PublicKeyOf(B64FromHex(bob_priv)) == B64FromHex(bob_pub));
  Key32 a{}, b{};
  CHECK(Base64Decode32(B64FromHex(alice_priv), &a) && Base64Decode32(B64FromHex(bob_pub), &b));
  Key32 s = X25519(a, b);
  CHECK(ToHex(std::vector<uint8_t>(s.begin(), s.end())) == shared);
  // RFC 4231 test case 2
  std::vector<uint8_t> jefe = {'J', 'e', 'f', 'e'};
  CHECK(ToHex(HmacSha256(jefe, "what do ya want for nothing?")) ==
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
  // Known-answer for the confirmation (HMAC computed independently with Python hmac/hashlib).
  const std::string transcript = "project-vpn:key-confirmation:v1:01234567890123456789012345678901:" +
                                 std::string("abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789");
  CHECK(ConfirmationAnswer(B64FromHex(alice_priv), B64FromHex(bob_pub), transcript) ==
        "1816ad35887104f7b3315300b26283e3f2917aa16a20e5a8fa115734509f9809");
  CHECK(ConfirmationAnswer(B64FromHex(bob_priv), B64FromHex(alice_pub), transcript) ==
        ConfirmationAnswer(B64FromHex(alice_priv), B64FromHex(bob_pub), transcript));
  // Generated keys agree in both directions.
  std::string k1 = GenerateDevicePrivateKey(), k2 = GenerateDevicePrivateKey();
  CHECK(k1 != k2 && k1.size() == 44);
  CHECK(ConfirmationAnswer(k1, PublicKeyOf(k2), transcript) == ConfirmationAnswer(k2, PublicKeyOf(k1), transcript));
  // Fail closed
  CHECK(Throws<std::invalid_argument>([&] { ConfirmationAnswer(k1, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", transcript); }));
  CHECK(Throws<std::invalid_argument>([&] { ConfirmationAnswer(k1, PublicKeyOf(k2), "arbitrary-message"); }));
  CHECK(Throws<std::invalid_argument>([&] { ConfirmationAnswer(k1, "short", transcript); }));
  CHECK(Throws<std::invalid_argument>([&] { ConfirmationAnswer("short", PublicKeyOf(k2), transcript); }));
  CHECK(Throws<std::invalid_argument>([&] { ConfirmationAnswer(k1, PublicKeyOf(k2), transcript + "0"); }));
  // base64: canonical only
  Key32 tmp{};
  CHECK(!Base64Decode32(std::string(43, 'A') + "B=", &tmp));  // non-zero trailing bits
  CHECK(!Base64Decode32(std::string(44, 'A'), &tmp));         // no pad
  CHECK(Base64Decode32(std::string(43, 'A') + "=", &tmp));
}

// ---- vault ---------------------------------------------------------------------------------------------

void VaultTests() {
  fs::path dir = FreshDir("vault");
  SecureVault vault(dir);
  CHECK(!vault.Read("session"));
  vault.Write("session", std::string("origin\thttps://example.org\nrefresh_token\tSECRET-TOKEN-VALUE\n"));
  CHECK(vault.Read("session").value_or("").find("SECRET-TOKEN-VALUE") != std::string::npos);
  // the file is DPAPI-protected: no plaintext on disk
  std::ifstream in(dir / "session.vault", std::ios::binary);
  std::string raw((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  in.close();
  CHECK(!raw.empty() && raw[0] == 1 && raw.find("SECRET-TOKEN-VALUE") == std::string::npos);
  // record name is bound into the blob: a copy under another name cannot be read
  fs::copy_file(dir / "session.vault", dir / "login.vault");
  CHECK(Throws<std::runtime_error>([&] { vault.Read("login"); }));
  // tamper
  {
    std::string bad = raw;
    bad[bad.size() / 2] ^= 0x55;
    std::ofstream out(dir / "session.vault", std::ios::binary | std::ios::trunc);
    out.write(bad.data(), static_cast<std::streamsize>(bad.size()));
  }
  CHECK(Throws<std::runtime_error>([&] { vault.Read("session"); }));
  vault.Write("session", std::nullopt);
  CHECK(!vault.Read("session") && !fs::exists(dir / "session.vault"));
  CHECK(Throws<std::invalid_argument>([&] { vault.Write("../escape", std::string("x")); }));
  CHECK(Throws<std::invalid_argument>([&] { vault.Read("other"); }));
  CHECK(Throws<std::invalid_argument>([&] { vault.Write("session", std::string(16385, 'x')); }));
  // device key: generated once, then stable
  std::string first = vault.DevicePrivateKey();
  CHECK(first == vault.DevicePrivateKey() && first.size() == 44);
  CHECK(SecureVault(dir).DevicePrivateKey() == first);
  vault.Write("device", std::string("not-a-key"));
  CHECK(Throws<std::runtime_error>([&] { vault.DevicePrivateKey(); }));
  std::error_code ec;
  fs::remove_all(dir, ec);
}

// ---- window deadline (vectors from WindowDeadlineTest.java) ---------------------------------------

void WindowTests() {
  WindowDeadline lease;
  lease.Start("connection", "runtime", 1, 80000, 0);
  lease.Advance("connection", "runtime", 2, 110000, 30000);
  lease.Advance("connection", "runtime", 2, 120000, 40000);  // replay must not extend
  CHECK(lease.deadline == 110000);
  WindowDeadline w;
  w.Start("a", "r", 1, 80000, 0);
  CHECK(Throws<std::logic_error>([&] { w.Advance("b", "r", 2, 100000, 30000); }));
  CHECK(Throws<std::logic_error>([&] { w.Advance("a", "s", 2, 100000, 30000); }));
  CHECK(Throws<std::logic_error>([&] { w.Advance("a", "r", 3, 100000, 30000); }));
  CHECK(Throws<std::logic_error>([&] { w.Advance("a", "r", 2, 160000, 80000); }));  // expired
  w.Clear();
  CHECK(Throws<std::logic_error>([&] { w.Advance("a", "r", 2, 100000, 30000); }));  // stopped
  WindowDeadline v;
  CHECK(Throws<std::logic_error>([&] { v.Start("a", "r", 1, 90001, 0); }));
  v.Start("a", "r", 1, 80000, 0);
  CHECK(Throws<std::logic_error>([&] { v.Advance("a", "r", 2, 80000, 30000); }));  // not increasing
  CHECK(Throws<std::logic_error>([&] { v.Start("a", "r", 1, 80000, 0); }));        // double start
}

// ---- engine process ------------------------------------------------------------------------------------

void StatsParseTests() {
  auto ok = ParsePeerStatsLine("PEER_STATS {\"peer_count\":1,\"last_handshake_time_sec\":1700000000,\"rx_bytes\":12,\"tx_bytes\":34}");
  CHECK(ok && ok->last_handshake_unix == 1700000000 && ok->rx_bytes == 12 && ok->tx_bytes == 34);
  CHECK(!ParsePeerStatsLine("{\"peer_count\":1}"));
  CHECK(!ParsePeerStatsLine("PEER_STATS {\"peer_count\":2,\"last_handshake_time_sec\":1,\"rx_bytes\":1,\"tx_bytes\":1}"));
  CHECK(!ParsePeerStatsLine("PEER_STATS {\"peer_count\":1,\"last_handshake_time_sec\":-1,\"rx_bytes\":1,\"tx_bytes\":1}"));
  CHECK(!ParsePeerStatsLine("PEER_STATS {\"peer_count\":1,\"rx_bytes\":1,\"tx_bytes\":1}"));
  CHECK(!ParsePeerStatsLine("PEER_STATS {\"peer_count\":1,\"last_handshake_time_sec\":99999999999999999999,\"rx_bytes\":1,\"tx_bytes\":1}"));
}

void EngineTests() {
  fs::path dir = FreshDir("engine");
  fs::path marker = dir / "graceful.txt";
  SetEnvironmentVariableW(L"FAKE_ENGINE_MARKER", marker.c_str());
  fs::path fake = g_tests_dir / "fake_engine.exe";
  {
    EngineProcess engine;
    engine.Start(fake.wstring(), L"run -c stdin", "config-that-would-hold-a-key", true);
    CHECK(engine.Running());
    for (int i = 0; i < 40 && !engine.Stats(); ++i) Sleep(100);
    auto stats = engine.Stats();
    CHECK(stats && stats->rx_bytes == 1000 && stats->tx_bytes == 500 && stats->last_handshake_unix > 0);
    engine.Stop(std::chrono::seconds(5));
    CHECK(!engine.Running() && !engine.Stats());
    CHECK(fs::exists(marker));  // Ctrl-C reached the child: graceful shutdown, not termination
  }
  // A child that ignores Ctrl-C is terminated after the grace period.
  SetEnvironmentVariableW(L"FAKE_ENGINE_IGNORE_CTRLC", L"1");
  {
    EngineProcess engine;
    engine.Start(fake.wstring(), L"run -c stdin", "x", true);
    DWORD pid = engine.ProcessId();
    HANDLE watch = OpenProcess(SYNCHRONIZE, FALSE, pid);
    auto begin = std::chrono::steady_clock::now();
    engine.Stop(std::chrono::milliseconds(800));
    CHECK(WaitForSingleObject(watch, 0) == WAIT_OBJECT_0);
    CHECK(std::chrono::steady_clock::now() - begin >= std::chrono::milliseconds(700));
    CloseHandle(watch);
  }
  // Destroying the object stops the child as well (Job Object backstop).
  DWORD pid = 0;
  HANDLE watch = nullptr;
  {
    EngineProcess engine;
    engine.Start(fake.wstring(), L"run -c stdin", "x", false);
    pid = engine.ProcessId();
    watch = OpenProcess(SYNCHRONIZE, FALSE, pid);
  }
  CHECK(WaitForSingleObject(watch, 5000) == WAIT_OBJECT_0);
  CloseHandle(watch);
  SetEnvironmentVariableW(L"FAKE_ENGINE_IGNORE_CTRLC", nullptr);
  SetEnvironmentVariableW(L"FAKE_ENGINE_MARKER", nullptr);
  CHECK(Throws<std::runtime_error>([] { EngineProcess e; e.Start(L"C:\\does\\not\\exist.exe", L"", "", false); }));
  std::error_code ec;
  fs::remove_all(dir, ec);
}

// ---- controller ---------------------------------------------------------------------------------------------

const char* kProfile =
    "[Interface]\nAddress = 10.203.71.2/30\nDNS = 192.0.2.53\nMTU = 1280\nJc = 4\nJmin = 64\nJmax = 96\n"
    "S1 = 16\nS2 = 24\nS3 = 32\nS4 = 40\nH1 = 101234\nH2 = 202345\nH3 = 303456\nH4 = 404567\n"
    "[Peer]\nPublicKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\nEndpoint = 192.0.2.1:51901\n"
    "AllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n";

struct Rig {
  fs::path dir;
  bool admin = true;
  std::unique_ptr<TunnelController> c;
  explicit Rig(const char* name, bool with_engine = true, bool with_session = true) {
    dir = FreshDir(name);
    if (with_engine) {
      fs::copy_file(g_tests_dir / "fake_engine.exe", dir / "detour-engine.exe");
      std::ofstream(dir / "wintun.dll") << "placeholder";
    }
    ControllerHooks h;
    h.vault_dir = dir / "vault";
    h.prefs_path = dir / "prefs.txt";
    h.engine_dir = dir;
    h.own_exe = dir / "DetourVpn.exe";
    h.is_admin = [this] { return admin; };
    h.network_available = [] { return true; };
    h.now_ms = [] {
      return static_cast<int64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                      std::chrono::steady_clock::now().time_since_epoch()).count());
    };
    h.unix_seconds = [] { return static_cast<int64_t>(std::time(nullptr)); };
    h.resolve_ipv4 = [](const std::string& host) {
      return host == "api.example.test" ? std::vector<std::string>{"198.51.100.9"} : std::vector<std::string>{};
    };
    h.start_probe = std::chrono::milliseconds(300);
    h.stop_grace = std::chrono::seconds(5);
    c = std::make_unique<TunnelController>(h);
    if (with_session) c->WriteSession(TunnelController::Record{{"origin", "https://198.51.100.7"}, {"refresh_token", "t"}});
  }
  ~Rig() {
    c.reset();
    std::error_code ec;
    fs::remove_all(dir, ec);
  }
};

void ControllerTests() {
  const std::string transcript = "project-vpn:key-confirmation:v1:01234567890123456789012345678901:" +
                                 std::string(64, 'a');
  WindowGrant grant{"conn-1", "run-1", 1};
  {
    Rig r("ctl-main");
    CHECK(TunnelController::Capabilities().size() == 6);
    std::string pub = r.c->PublicKey();
    CHECK(pub == r.c->PublicKey() && pub.size() == 44);  // stable, from the vault
    CHECK(r.c->Prove(PublicKeyOf(GenerateDevicePrivateKey()), transcript).size() == 64);
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->Prove("bad", transcript); }));
    CHECK(r.c->Permission());
    // records and node
    auto s = r.c->ReadSession();
    CHECK(s && s->at("origin") == "https://198.51.100.7" && s->size() == 2);
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteSession(TunnelController::Record{{"origin", "x"}}); }));
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteSession(TunnelController::Record{{"origin", "a\nb"}, {"refresh_token", "t"}}); }));
    r.c->WriteSession(std::nullopt);
    CHECK(!r.c->ReadSession());
    CHECK(!r.c->ReadNode());
    r.c->WriteNode("0123abcd-0123-4abc-8abc-0123456789ab");
    CHECK(r.c->ReadNode() == "0123abcd-0123-4abc-8abc-0123456789ab");
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteNode("not-a-uuid"); }));
    r.c->WriteNode(std::nullopt);
    CHECK(!r.c->ReadNode());
    // DNS choice (ADR-106): default "", listed resolvers only, survives with split settings
    CHECK(r.c->Dns().empty());
    CHECK(r.c->WriteDns("9.9.9.9") == "9.9.9.9" && r.c->Dns() == "9.9.9.9");
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteDns("10.0.0.1"); }));
    CHECK(r.c->Dns() == "9.9.9.9");
    r.c->WriteNode(std::nullopt);
    CHECK(r.c->Dns() == "9.9.9.9");  // other prefs writes keep it
    CHECK(r.c->WriteDns("").empty() && r.c->Dns().empty());
    // split settings
    CHECK(r.c->SplitMode() == "exclude" && r.c->SplitLastMode() == "exclude" && r.c->SplitApps().empty());
    fs::path app = r.dir / "tool.exe";
    std::ofstream(app) << "x";
    auto domains = r.c->WriteSplit("include", {app.string()}, {"*.Example.ru", "exact.example.net"});
    CHECK(domains == std::vector<std::string>({"*.example.ru", "exact.example.net"}));
    CHECK(r.c->SplitMode() == "include" && r.c->SplitLastMode() == "include" && r.c->SplitApps().size() == 1);
    r.c->WriteSplit("all", {}, {});
    CHECK(r.c->SplitMode() == "all" && r.c->SplitLastMode() == "include");  // last split mode survives
    CHECK(ThrowsCode("domain_invalid", [&] { r.c->WriteSplit("exclude", {}, {"https://example.ru"}); }));
    CHECK(ThrowsCode("domain_duplicate", [&] { r.c->WriteSplit("exclude", {}, {"Example.ru", "example.ru."}); }));
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteSplit("bogus", {}, {}); }));
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteSplit("exclude", {(r.dir / "missing.exe").string()}, {}); }));
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteSplit("exclude", {(r.dir / "DetourVpn.exe").string()}, {}); }));  // own exe
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteSplit("exclude", {app.string(), app.string()}, {}); }));
    r.c->WriteSplit("exclude", {}, {"example.ru"});
  }
  {
    // fail-closed start paths
    Rig missing("ctl-noengine", false);
    CHECK(ThrowsCode("native_engine_unavailable", [&] { missing.c->Connect(kProfile, 30000, std::nullopt); }));
    Rig r("ctl-start");
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->Connect(kProfile, 0, std::nullopt); }));
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->Connect(kProfile, 60001, std::nullopt); }));
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->Connect(kProfile, 90001, grant); }));
    r.admin = false;
    CHECK(ThrowsCode("native_permission_error", [&] { r.c->Connect(kProfile, 30000, std::nullopt); }));
    r.admin = true;
    CHECK(ThrowsCode("native_config_invalid", [&] { r.c->Connect("[Interface]\nPrivateKey = x\n", 30000, std::nullopt); }));
    std::string ipv6 = kProfile;
    ipv6.replace(ipv6.find("0.0.0.0/0"), 9, "0.0.0.0/0, ::/0");
    CHECK(ThrowsCode("native_config_invalid", [&] { r.c->Connect(ipv6, 30000, std::nullopt); }));
    r.c->WriteSplit("include", {}, {});  // empty include would be fail-open
    CHECK(ThrowsCode("native_config_invalid", [&] { r.c->Connect(kProfile, 30000, std::nullopt); }));
    r.c->WriteSplit("all", {}, {});
    r.c->WriteSession(std::nullopt);  // no control-plane address to bypass
    CHECK(ThrowsCode("native_config_invalid", [&] { r.c->Connect(kProfile, 30000, std::nullopt); }));
    CHECK(!r.c->Snapshot().up);
  }
  {
    // full lifecycle against the fake engine
    Rig r("ctl-life");
    fs::path marker = r.dir / "graceful.txt";
    SetEnvironmentVariableW(L"FAKE_ENGINE_MARKER", marker.c_str());
    CHECK(!r.c->Snapshot().handshake_seen);
    r.c->Connect(kProfile, 60000, grant);
    TunnelSnapshot s = r.c->Snapshot();
    CHECK(s.up && s.network && s.remaining_ms > 0 && s.remaining_ms <= 60000);
    for (int i = 0; i < 30 && r.c->Snapshot().handshake_age < 0; ++i) Sleep(100);
    s = r.c->Snapshot();
    CHECK(s.handshake_age >= 2 && s.handshake_age <= 6);  // the fake reports "3 s ago"
    CHECK(s.handshake_seen);  // Task 7: native memory of this connection's handshake
    CHECK(s.received_bytes == 1000 && s.traffic_bytes == 1500);
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->Connect(kProfile, 30000, grant); }));  // already up
    // ADR-187: split rules are stored while up; the running tunnel is not touched.
    r.c->WriteSplit("all", {}, {});
    CHECK(r.c->SplitMode() == "all" && r.c->Snapshot().up);
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteDns("1.1.1.1"); }));                   // locked while up
    r.c->AdvanceWindow(WindowGrant{"conn-1", "run-1", 2}, 80000);
    CHECK(r.c->Snapshot().remaining_ms > 60000);
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->AdvanceWindow(WindowGrant{"other", "run-1", 3}, 80000); }));
    r.c->Disconnect();
    s = r.c->Snapshot();
    CHECK(!s.up && s.remaining_ms == 0 && s.handshake_age == -1);
    CHECK(s.handshake_seen);  // kept after the tunnel stops, until the next connect
    CHECK(fs::exists(marker));
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->AdvanceWindow(WindowGrant{"conn-1", "run-1", 3}, 80000); }));
    SetEnvironmentVariableW(L"FAKE_ENGINE_MARKER", nullptr);
  }
  {
    // lease expiry stops the engine on its own
    Rig r("ctl-expire");
    r.c->Connect(kProfile, 1200, grant);
    CHECK(r.c->Snapshot().up);
    for (int i = 0; i < 40 && r.c->Snapshot().up; ++i) Sleep(100);
    TunnelSnapshot s = r.c->Snapshot();
    CHECK(!s.up && s.reason == "lease_expired");
  }
  {
    // an engine that dies by itself is reported, not hidden
    Rig r("ctl-dies");
    // The fake engine exits by itself 1.5 s after start (after the controller's 300 ms start probe).
    SetEnvironmentVariableW(L"FAKE_ENGINE_EXIT_MS", L"1500");
    r.c->Connect(kProfile, 60000, std::nullopt);
    SetEnvironmentVariableW(L"FAKE_ENGINE_EXIT_MS", nullptr);
    CHECK(r.c->Snapshot().up);
    for (int i = 0; i < 60 && r.c->Snapshot().up; ++i) Sleep(100);
    TunnelSnapshot s = r.c->Snapshot();
    CHECK(!s.up && s.reason == "engine_exited" && s.remaining_ms == 0);
  }
  {
    // ADR-115 panel server (VLESS + Reality); obviously fake values, reserved test names
    VlessRealityProfile v;
    v.server = "panel.example.test";
    v.port = 443;
    v.uuid = "00000000-0000-4000-8000-000000000001";
    v.flow = "xtls-rprx-vision";
    v.sni = "www.example.test";
    v.public_key = std::string(42, 'r') + "A";
    v.short_id = "0a1b";
    v.fingerprint = "chrome";
    Rig missing("ctl-vless-noengine", false);
    CHECK(ThrowsCode("native_engine_unavailable", [&] { missing.c->ConnectVless(v, 60000); }));
    Rig r("ctl-vless");
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->ConnectVless(v, 0); }));
    CHECK(ThrowsCode("native_service_state_invalid",
                     [&] { r.c->ConnectVless(v, TunnelController::kMaxVlessMs + 1); }));
    r.admin = false;
    CHECK(ThrowsCode("native_permission_error", [&] { r.c->ConnectVless(v, 60000); }));
    r.admin = true;
    VlessRealityProfile bad = v;
    bad.public_key = "short";
    CHECK(ThrowsCode("native_config_invalid", [&] { r.c->ConnectVless(bad, 60000); }));
    CHECK(!r.c->Snapshot().up);
    r.c->WriteDns("8.8.8.8");
    r.c->ConnectVless(v, TunnelController::kMaxVlessMs);
    TunnelSnapshot s = r.c->Snapshot();
    CHECK(s.up && s.remaining_ms > 60000 && s.remaining_ms <= TunnelController::kMaxVlessMs);
    // One tunnel at a time, either kind; settings stay locked while up.
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->ConnectVless(v, 60000); }));
    CHECK(ThrowsCode("native_service_state_invalid", [&] { r.c->Connect(kProfile, 30000, grant); }));
    CHECK(ThrowsCode("native_operation_failed", [&] { r.c->WriteDns("1.1.1.1"); }));
    r.c->Disconnect();
    s = r.c->Snapshot();
    CHECK(!s.up && s.remaining_ms == 0);
  }
}

}  // namespace

int main(int, char** argv) {
  wchar_t self[MAX_PATH];
  GetModuleFileNameW(nullptr, self, MAX_PATH);
  g_tests_dir = fs::path(self).parent_path();
  (void)argv;
  std::setvbuf(stdout, nullptr, _IONBF, 0);
  std::puts("keys");
  KeyTests();
  std::puts("vault");
  VaultTests();
  std::puts("window");
  WindowTests();
  StatsParseTests();
  std::puts("engine");
  EngineTests();
  std::puts("controller");
  ControllerTests();
  std::printf("%d checks, %d failures\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}

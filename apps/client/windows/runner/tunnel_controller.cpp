#include "tunnel_controller.h"

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <fstream>
#include <set>
#include <sstream>

#include "config_factory.h"
#include "domain_rule.h"
#include "key_confirmation.h"
#include "profile.h"

namespace detour {
namespace {

const char* const kGenericMessage = "Операция VPN не выполнена";

NativeError Generic(const char* code = "native_operation_failed") {
  return NativeError(code, kGenericMessage);
}

bool IsUuid(const std::string& s) {
  static const int kGroups[] = {8, 4, 4, 4, 12};
  size_t at = 0;
  for (int g = 0; g < 5; ++g) {
    if (g > 0) {
      if (at >= s.size() || s[at] != '-') return false;
      ++at;
    }
    for (int i = 0; i < kGroups[g]; ++i, ++at)
      if (at >= s.size() || !((s[at] >= '0' && s[at] <= '9') || (s[at] >= 'a' && s[at] <= 'f'))) return false;
  }
  return at == s.size();
}

bool CleanValue(const std::string& v) {
  return v.size() <= 4096 &&
         std::none_of(v.begin(), v.end(), [](unsigned char c) { return c < 0x20 || c == 0x7f; });
}

std::string Lower(std::string s) {
  for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

}  // namespace

std::vector<std::string> TunnelController::Capabilities() {
  return {"awg3", "userspace", "ipv4-full-route", "ephemeral-device-key", "app-split.v1",
          "domain-routing.v1"};
}

TunnelController::TunnelController(ControllerHooks hooks)
    : hooks_(std::move(hooks)), vault_(hooks_.vault_dir) {
  watcher_ = std::thread([this]() { WatchLoop(); });
}

TunnelController::~TunnelController() {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
    StopLocked();
  }
  wake_.notify_all();
  if (watcher_.joinable()) watcher_.join();
}

void TunnelController::WatchLoop() {
  std::unique_lock<std::mutex> lock(mutex_);
  while (!stopping_) {
    wake_.wait_for(lock, std::chrono::milliseconds(250));
    if (stopping_) break;
    if (engine_started_ && !engine_.Running()) {
      // Engine died on its own: the TUN is gone. Clear state so the UI sees "down".
      reason_ = "engine_exited";
      StopLocked();
    } else if (deadline_ > 0 && hooks_.now_ms() >= deadline_) {
      reason_ = "lease_expired";
      StopLocked();
    }
  }
}

void TunnelController::StopLocked() {
  engine_.Stop(hooks_.stop_grace);
  engine_started_ = false;
  deadline_ = 0;
  window_.Clear();
}

bool TunnelController::Permission() { return hooks_.is_admin(); }

std::string TunnelController::PrivateKey() {
  if (!key_cache_) key_cache_ = vault_.DevicePrivateKey();
  return *key_cache_;
}

std::string TunnelController::PublicKey() {
  std::lock_guard<std::mutex> lock(mutex_);
  try {
    return PublicKeyOf(PrivateKey());
  } catch (const std::exception&) {
    throw Generic();
  }
}

std::string TunnelController::Prove(const std::string& ephemeral_key, const std::string& transcript) {
  std::lock_guard<std::mutex> lock(mutex_);
  try {
    return ConfirmationAnswer(PrivateKey(), ephemeral_key, transcript);
  } catch (const std::exception&) {
    throw Generic();
  }
}

// ---- records and preferences ----------------------------------------------------------------------

std::optional<TunnelController::Record> TunnelController::ReadRecord(
    const std::string& name, const std::vector<std::string>& fields) {
  std::lock_guard<std::mutex> lock(mutex_);
  try {
    auto raw = vault_.Read(name);
    if (!raw) return std::nullopt;
    Record record;
    std::stringstream stream(*raw);
    std::string line;
    while (std::getline(stream, line)) {
      size_t tab = line.find('\t');
      if (tab == std::string::npos) throw std::runtime_error("vault_corrupt");
      record[line.substr(0, tab)] = line.substr(tab + 1);
    }
    for (const auto& field : fields)
      if (!record.count(field)) throw std::runtime_error("vault_corrupt");
    Record out;
    for (const auto& field : fields) out[field] = record[field];
    return out;
  } catch (const std::exception&) {
    throw Generic();
  }
}

void TunnelController::WriteRecord(const std::string& name, const std::vector<std::string>& fields,
                                   const std::optional<Record>& value) {
  std::lock_guard<std::mutex> lock(mutex_);
  try {
    if (!value) {
      vault_.Write(name, std::nullopt);
      return;
    }
    std::string text;
    for (const auto& field : fields) {
      auto it = value->find(field);
      if (it == value->end() || it->second.empty() || !CleanValue(it->second))
        throw std::invalid_argument("record_invalid");
      text += field + "\t" + it->second + "\n";
    }
    if (value->size() != fields.size()) throw std::invalid_argument("record_invalid");
    vault_.Write(name, text);
  } catch (const std::exception&) {
    throw Generic();
  }
}

std::optional<TunnelController::Record> TunnelController::ReadSession() {
  return ReadRecord("session", {"origin", "refresh_token"});
}
void TunnelController::WriteSession(const std::optional<Record>& value) {
  WriteRecord("session", {"origin", "refresh_token"}, value);
}
std::optional<TunnelController::Record> TunnelController::ReadPendingLogin() {
  return ReadRecord("login", {"request_id", "verifier", "bot_url", "expires_at"});
}
void TunnelController::WritePendingLogin(const std::optional<Record>& value) {
  WriteRecord("login", {"request_id", "verifier", "bot_url", "expires_at"}, value);
}

TunnelController::Prefs TunnelController::LoadPrefs() const {
  Prefs prefs;
  std::ifstream in(hooks_.prefs_path, std::ios::binary);
  std::string line;
  std::set<std::string> seen_apps, seen_domains;
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    size_t tab = line.find('\t');
    if (tab == std::string::npos) continue;
    std::string key = line.substr(0, tab), value = line.substr(tab + 1);
    if (!CleanValue(value)) continue;
    if (key == "node" && IsUuid(value)) prefs.node = value;
    else if (key == "mode" && (value == "all" || value == "exclude" || value == "include")) prefs.mode = value;
    else if (key == "last_mode" && (value == "exclude" || value == "include")) prefs.last_mode = value;
    else if (key == "app" && IsValidAppPath(value) && seen_apps.insert(Lower(value)).second)
      prefs.apps.push_back(value);
    else if (key == "domain" && seen_domains.insert(value).second) prefs.domains.push_back(value);
    else if (key == "dns" && IsDnsChoice(value)) prefs.dns = value;  // anything else = default
  }
  return prefs;
}

void TunnelController::SavePrefs(const Prefs& prefs) const {
  std::string text;
  if (!prefs.node.empty()) text += "node\t" + prefs.node + "\n";
  text += "mode\t" + prefs.mode + "\n";
  if (!prefs.last_mode.empty()) text += "last_mode\t" + prefs.last_mode + "\n";
  for (const auto& app : prefs.apps) text += "app\t" + app + "\n";
  for (const auto& domain : prefs.domains) text += "domain\t" + domain + "\n";
  if (!prefs.dns.empty()) text += "dns\t" + prefs.dns + "\n";
  std::error_code ec;
  std::filesystem::create_directories(hooks_.prefs_path.parent_path(), ec);
  std::filesystem::path temp = hooks_.prefs_path;
  temp += L".tmp";
  {
    std::ofstream out(temp, std::ios::binary | std::ios::trunc);
    out.write(text.data(), static_cast<std::streamsize>(text.size()));
    out.flush();
    if (!out.good()) throw Generic();
  }
  if (!MoveFileExW(temp.c_str(), hooks_.prefs_path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
    std::filesystem::remove(temp, ec);
    throw Generic();
  }
}

std::optional<std::string> TunnelController::ReadNode() {
  std::lock_guard<std::mutex> lock(mutex_);
  Prefs prefs = LoadPrefs();
  if (prefs.node.empty()) return std::nullopt;
  return prefs.node;
}

void TunnelController::WriteNode(const std::optional<std::string>& id) {
  if (id && !IsUuid(*id)) throw Generic();
  std::lock_guard<std::mutex> lock(mutex_);
  Prefs prefs = LoadPrefs();
  prefs.node = id.value_or("");
  SavePrefs(prefs);
}

std::vector<std::string> TunnelController::SplitApps() {
  std::lock_guard<std::mutex> lock(mutex_);
  return LoadPrefs().apps;
}
std::vector<std::string> TunnelController::SplitDomains() {
  std::lock_guard<std::mutex> lock(mutex_);
  return LoadPrefs().domains;
}
std::string TunnelController::SplitMode() {
  std::lock_guard<std::mutex> lock(mutex_);
  return LoadPrefs().mode;
}
std::string TunnelController::SplitLastMode() {
  std::lock_guard<std::mutex> lock(mutex_);
  Prefs prefs = LoadPrefs();
  if (prefs.last_mode == "exclude" || prefs.last_mode == "include") return prefs.last_mode;
  return prefs.mode == "include" ? "include" : "exclude";
}

std::vector<std::string> TunnelController::WriteSplit(const std::string& mode,
                                                      const std::vector<std::string>& apps,
                                                      const std::vector<std::string>& domains) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (engine_started_ || deadline_ != 0) throw Generic();  // never change rules while connected
  if ((mode != "all" && mode != "exclude" && mode != "include") || apps.size() > 63 || domains.size() > 128)
    throw Generic();
  std::set<std::string> seen_apps;
  std::string own = Lower(hooks_.own_exe.string());
  for (const auto& app : apps) {
    std::error_code ec;
    if (!IsValidAppPath(app) || Lower(app) == own || !seen_apps.insert(Lower(app)).second ||
        !std::filesystem::is_regular_file(std::filesystem::path(app), ec))
      throw Generic();
  }
  std::set<std::string> canonical;
  for (const auto& raw : domains) {
    try {
      DomainRule rule = ParseDomainRule(raw);
      if (!canonical.insert(rule.Canonical()).second)
        throw NativeError("domain_duplicate", "Такой домен уже добавлен");
    } catch (const std::invalid_argument&) {
      throw NativeError("domain_invalid", "Укажи домен или шаблон *.ru без URL и IP-адреса");
    }
  }
  Prefs prefs = LoadPrefs();
  if (mode == "exclude" || mode == "include") prefs.last_mode = mode;
  else if (prefs.last_mode != "exclude" && prefs.last_mode != "include")
    prefs.last_mode = prefs.mode == "include" ? "include" : "exclude";
  prefs.mode = mode;
  prefs.apps = apps;
  prefs.domains.assign(canonical.begin(), canonical.end());
  SavePrefs(prefs);
  return prefs.domains;
}

std::string TunnelController::Dns() {
  std::lock_guard<std::mutex> lock(mutex_);
  return LoadPrefs().dns;
}

std::string TunnelController::WriteDns(const std::string& server) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (engine_started_ || deadline_ != 0) throw Generic();  // like split: never while connected
  if (!server.empty() && !IsDnsChoice(server)) throw Generic();
  Prefs prefs = LoadPrefs();
  prefs.dns = server;
  SavePrefs(prefs);
  return prefs.dns;
}

// ---- connection -------------------------------------------------------------------------------------

std::vector<std::string> TunnelController::ControlPlaneAddresses() {
  auto session = vault_.Read("session");
  if (!session) throw NativeError("native_config_invalid", kGenericMessage);
  std::string origin;
  std::stringstream stream(*session);
  std::string line;
  while (std::getline(stream, line))
    if (line.rfind("origin\t", 0) == 0) origin = line.substr(7);
  static const std::string kScheme = "https://";
  if (origin.compare(0, kScheme.size(), kScheme) != 0) throw NativeError("native_config_invalid", kGenericMessage);
  std::string host = origin.substr(kScheme.size());
  host = host.substr(0, host.find_first_of("/:?#"));
  if (host.empty() || host.find_first_of("[]@ ") != std::string::npos)
    throw NativeError("native_config_invalid", kGenericMessage);
  if (IsIpv4Literal(host)) return {host};
  std::vector<std::string> resolved = hooks_.resolve_ipv4(host);
  if (resolved.empty() || resolved.size() > 8) throw NativeError("native_config_invalid", kGenericMessage);
  for (const auto& ip : resolved)
    if (!IsIpv4Literal(ip)) throw NativeError("native_config_invalid", kGenericMessage);
  return resolved;
}

void TunnelController::Connect(const std::string& config, int64_t remaining_ms,
                               const std::optional<WindowGrant>& grant) {
  std::lock_guard<std::mutex> lock(mutex_);
  const int64_t submitted = hooks_.now_ms();
  const bool renewable = grant.has_value();
  auto state_error = []() { return NativeError("native_service_state_invalid", kGenericMessage); };
  if (remaining_ms <= 0 || remaining_ms > (renewable ? 90000 : 60000) || engine_started_ || deadline_ != 0)
    throw state_error();
  if (renewable && (grant->connection.empty() || grant->runtime.empty())) throw state_error();
  if (!hooks_.is_admin()) throw NativeError("native_permission_error", kGenericMessage);

  const std::filesystem::path exe = hooks_.engine_dir / L"detour-engine.exe";
  if (!std::filesystem::is_regular_file(exe) || !std::filesystem::is_regular_file(hooks_.engine_dir / L"wintun.dll"))
    throw NativeError("native_engine_unavailable", kGenericMessage);

  std::string engine_config;
  try {
    Prefs prefs = LoadPrefs();
    AwgProfile profile = ParseProfile(config, PrivateKey());
    RoutingPolicy policy;
    policy.mode = prefs.mode;
    policy.app_paths = prefs.apps;
    policy.domains = prefs.domains;
    policy.api_ipv4 = ControlPlaneAddresses();
    policy.dns_server = prefs.dns;
    engine_config = CreateEngineConfig(profile, policy);
  } catch (const NativeError&) {
    throw;
  } catch (const std::invalid_argument&) {
    throw NativeError("native_config_invalid", kGenericMessage);
  } catch (const std::exception&) {
    throw NativeError("native_io_error", kGenericMessage);
  }

  const int64_t deadline = submitted + remaining_ms;
  if (hooks_.now_ms() >= deadline) throw state_error();
  try {
    if (renewable) window_.Start(grant->connection, grant->runtime, grant->generation, deadline, hooks_.now_ms());
  } catch (const std::logic_error&) {
    throw state_error();
  }
  deadline_ = deadline;
  reason_.clear();
  try {
    engine_.Start(exe.wstring(), L"run -c stdin", engine_config, true);
    engine_started_ = true;
  } catch (const std::exception&) {
    StopLocked();
    throw NativeError("native_start_failed", kGenericMessage);
  }
  std::fill(engine_config.begin(), engine_config.end(), '\0');

  // Fail early when the engine exits immediately (bad config, no wintun, TUN creation refused).
  const int64_t probe_end = hooks_.now_ms() + hooks_.start_probe.count();
  while (hooks_.now_ms() < probe_end) {
    if (!engine_.Running()) {
      StopLocked();
      throw NativeError("native_start_failed", kGenericMessage);
    }
    Sleep(50);
  }
  if (hooks_.now_ms() >= deadline_) {
    StopLocked();
    throw state_error();
  }
}

void TunnelController::AdvanceWindow(const WindowGrant& grant, int64_t remaining_ms) {
  std::lock_guard<std::mutex> lock(mutex_);
  const int64_t now = hooks_.now_ms();
  if (!engine_started_ || !engine_.Running() || remaining_ms <= 0 || remaining_ms > 90000)
    throw NativeError("native_service_state_invalid", kGenericMessage);
  try {
    window_.Advance(grant.connection, grant.runtime, grant.generation, now + remaining_ms, now);
  } catch (const std::logic_error&) {
    throw NativeError("native_service_state_invalid", kGenericMessage);
  }
  deadline_ = window_.deadline;
}

void TunnelController::Disconnect() {
  std::lock_guard<std::mutex> lock(mutex_);
  StopLocked();
}

TunnelSnapshot TunnelController::Snapshot() {
  std::lock_guard<std::mutex> lock(mutex_);
  TunnelSnapshot snapshot;
  if (engine_started_ && !engine_.Running()) {
    // Do not wait for the watcher: report the death together with its reason, and clear state.
    reason_ = "engine_exited";
    StopLocked();
  }
  snapshot.up = engine_started_ && engine_.Running();
  snapshot.network = hooks_.network_available ? hooks_.network_available() : false;
  snapshot.remaining_ms = std::max<int64_t>(0, deadline_ - hooks_.now_ms());
  if (snapshot.up) {
    if (auto stats = engine_.Stats()) {
      const auto age = std::chrono::steady_clock::now() - stats->received_at;
      if (age < std::chrono::seconds(5)) {  // stale counters never confirm a handshake
        int64_t now = hooks_.unix_seconds();
        if (stats->last_handshake_unix > 0 && stats->last_handshake_unix <= now)
          snapshot.handshake_age = now - stats->last_handshake_unix;
        snapshot.traffic_bytes = stats->rx_bytes + stats->tx_bytes;
        snapshot.received_bytes = stats->rx_bytes;
      }
    }
  }
  snapshot.reason = reason_;
  return snapshot;
}

}  // namespace detour

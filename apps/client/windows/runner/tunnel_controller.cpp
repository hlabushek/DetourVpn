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
    : hooks_(std::move(hooks)), vault_(hooks_.vault_dir), kill_switch_(hooks_.kill_switch_backend) {
  // ADR-196: startup sweep of rules a previous process of ours left (our provider key only). With
  // the setting off the firewall is never called.
  if (LoadPrefs().kill_switch) kill_switch_.Sweep();
  watcher_ = std::thread([this]() { WatchLoop(); });
}

TunnelController::~TunnelController() {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
    StopLocked();
    kill_switch_.Release();  // the app is closing: never leave a block behind
  }
  wake_.notify_all();
  if (watcher_.joinable()) watcher_.join();
}

void TunnelController::WatchLoop() {
  std::unique_lock<std::mutex> lock(mutex_);
  while (!stopping_) {
    wake_.wait_for(lock, std::chrono::milliseconds(250));
    if (stopping_) break;
    if (deadline_ > 0 && hooks_.now_ms() >= deadline_) {
      reason_ = "lease_expired";
      StopLocked();
    } else if (engine_started_ && !engine_.Running()) {
      EngineExitedLocked();
    } else if (engine_started_) {
      TrackTunnelLocked();
      if (!engine_started_) continue;  // the block could not follow the tunnel: stopped
      try {
        WatchTickLocked();
      } catch (const std::exception&) {
        // A watchdog fault must never take the tunnel down by itself.
      }
    }
  }
}

void TunnelController::TrackTunnelLocked() {
  if (!kill_switch_.engaged() || !hooks_.tunnel_luid) return;
  const int64_t now = hooks_.now_ms();
  const auto& applied = *kill_switch_.rules();
  // Poll often until the new adapter is permitted, then once a second to follow a change.
  if (applied.tunnel_luid && now < next_luid_poll_) return;
  next_luid_poll_ = now + 1000;
  std::optional<uint64_t> luid;
  try {
    luid = hooks_.tunnel_luid();
  } catch (const std::exception&) {
    return;
  }
  if (!luid || luid == applied.tunnel_luid) return;
  KillSwitchRules rules = applied;
  rules.tunnel_luid = luid;
  if (!kill_switch_.Engage(rules)) {
    // The tunnel cannot be permitted, so it is useless behind the block: report it plainly. The
    // UI's recovery treats this as terminal and ends the intent, which removes the block.
    reason_ = "kill_switch_failed";
    StopLocked();
  }
}

void TunnelController::EngageForConnectLocked(bool enabled, const std::vector<std::string>& api_ipv4) {
  if (!enabled) {
    kill_switch_.Release();  // no backend call when nothing is engaged
    return;
  }
  KillSwitchRules rules;
  rules.engine_exe = hooks_.engine_dir / L"detour-engine.exe";
  rules.own_exe = hooks_.own_exe;
  rules.api_ipv4 = api_ipv4;
  if (!kill_switch_.Engage(rules))
    throw NativeError("kill_switch_failed",
                      "Не удалось включить блокировку трафика при обрыве. VPN не подключён.");
}

bool TunnelController::KillSwitchEnabled() {
  std::lock_guard<std::mutex> lock(mutex_);
  return LoadPrefs().kill_switch;
}

bool TunnelController::WriteKillSwitch(bool enabled) {
  std::lock_guard<std::mutex> lock(mutex_);
  Prefs prefs = LoadPrefs();
  prefs.kill_switch = enabled;
  SavePrefs(prefs);
  if (!enabled) kill_switch_.Release();
  return prefs.kill_switch;
}

void TunnelController::EndIntent() {
  std::lock_guard<std::mutex> lock(mutex_);
  kill_switch_.Release();
}

bool TunnelController::KillSwitchActive() {
  std::lock_guard<std::mutex> lock(mutex_);
  return kill_switch_.engaged();
}

void TunnelController::StopLocked() {
  engine_.Stop(hooks_.stop_grace);
  engine_started_ = false;
  deadline_ = 0;
  window_.Clear();
  watchdog_.TunnelDown();
  restartable_ = false;
  std::fill(awg_config_.begin(), awg_config_.end(), '\0');
  awg_config_.clear();
  awg_policy_ = RoutingPolicy{};
  // ADR-196: the block stays while the user still wants the VPN (recovery follows); only the
  // permit for the adapter that just went away is dropped. A failure keeps the old rules: the
  // stale permit names an interface that no longer exists, so nothing leaks through it.
  if (kill_switch_.engaged() && kill_switch_.rules()->tunnel_luid) {
    KillSwitchRules rules = *kill_switch_.rules();
    rules.tunnel_luid.reset();
    kill_switch_.Engage(rules);
  }
}

void TunnelController::EngineExitedLocked() {
  // ADR-194: the engine died on its own (the TUN is gone with it). Restart it in place on the same
  // window while the crash budget allows; otherwise report "down" so the UI's recovery (a new
  // lease, ADR-099) takes over. The user's wish to stay connected lives in Dart and is kept.
  const int64_t now = hooks_.now_ms();
  const int64_t window = hooks_.crash_window.count();
  crash_restart_times_.erase(std::remove_if(crash_restart_times_.begin(), crash_restart_times_.end(),
                                            [&](int64_t at) { return now - at >= window; }),
                             crash_restart_times_.end());
  if (restartable_ && static_cast<int>(crash_restart_times_.size()) < hooks_.crash_restarts) {
    crash_restart_times_.push_back(now);
    RestartInPlaceLocked("engine_exited");  // on failure it stops with that reason
    return;
  }
  reason_ = "engine_exited";
  StopLocked();
}

bool TunnelController::RestartInPlaceLocked(const char* failure_reason) {
  try {
    engine_.Stop(hooks_.stop_grace);
    engine_started_ = false;
    std::string engine_config = CreateEngineConfig(ParseProfile(awg_config_, PrivateKey()), awg_policy_);
    LaunchLocked(hooks_.engine_dir / L"detour-engine.exe", engine_config, false);
    ++restarts_in_place_;
    return true;
  } catch (const std::exception&) {
    // LaunchLocked already stopped on its own failures; a config failure stops here.
    reason_ = failure_reason;
    StopLocked();
    return false;
  }
}

void TunnelController::WatchTickLocked() {
  if (!restartable_) return;  // VLESS (ADR-115): no handshake evidence, so no stall rule
  const int64_t now = hooks_.now_ms();
  if (now >= next_network_poll_) {
    next_network_poll_ = now + hooks_.network_poll.count();
    if (hooks_.physical_network) {
      std::string id = hooks_.physical_network();
      network_usable_ = !id.empty();
      // A different network, or a network back after a loss: the path may have moved.
      if (network_usable_ && id != network_id_) watchdog_.NetworkChanged(now);
      network_id_ = id;
    }
  }
  int64_t age_ms = -1;
  if (auto stats = engine_.Stats()) {
    stats_seen_ = true;
    const int64_t age_s = handshake_clock_.AgeSeconds(stats->last_handshake_unix, hooks_.unix_seconds(), now);
    if (age_s >= 0) age_ms = age_s * 1000;
  }
  // Without peer stats (an engine build without them) the handshake rules would restart a working
  // tunnel every few minutes, so they stay off; the crash restart above still applies.
  if (!stats_seen_) return;
  switch (watchdog_.Decide(now, age_ms, network_usable_)) {
    case TunnelWatchdog::Action::kRestart:
      watchdog_.Restarted(now);
      RestartInPlaceLocked("native_restart_failed");
      break;
    case TunnelWatchdog::Action::kNewLease:
      // Two restarts after a network change got no handshake: give the window up so the UI's
      // recovery starts a new one (same code as Android's watchdog_new_lease).
      reason_ = "handshake_not_established";
      StopLocked();
      break;
    case TunnelWatchdog::Action::kNone:
      break;
  }
}

void TunnelController::SystemResumed() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!engine_started_ || !restartable_) return;
  const int64_t now = hooks_.now_ms();
  watchdog_.NetworkChanged(now);
  next_network_poll_ = now;  // re-read the network at the next tick
  wake_.notify_all();
}

int TunnelController::RestartsInPlace() {
  std::lock_guard<std::mutex> lock(mutex_);
  return restarts_in_place_;
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
    else if (key == "kill_switch") prefs.kill_switch = value == "1";  // ADR-196: anything else = off
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
  if (prefs.kill_switch) text += "kill_switch\t1\n";
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
  // ADR-187: allowed while connected. Only the stored rules change; the running engine keeps the
  // config built at its connect, and the next connect reads these prefs.
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
  // Like split (ADR-187): allowed while connected. Only the stored choice changes; the running
  // engine keeps the DNS of the config built at its connect, and the next connect reads these prefs.
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
  std::vector<std::string> addresses;
  if (IsIpv4Literal(host)) {
    addresses.push_back(host);
  } else {
    addresses = ResolveApiHost(host);
    if (addresses.empty() || addresses.size() > 8) throw NativeError("native_config_invalid", kGenericMessage);
    for (const auto& ip : addresses)
      if (!IsIpv4Literal(ip)) throw NativeError("native_config_invalid", kGenericMessage);
  }
  // ADR-195: fallback origins bypass the tunnel too, so the API stays reachable through them when
  // the primary host or the tunnel fails. Best effort: a fallback that does not resolve now (or
  // answers with something unusable) is skipped; it never blocks a connect the primary allows.
  for (const auto& fallback : api_fallback_hosts_) {
    std::vector<std::string> found;
    if (IsIpv4Literal(fallback)) {
      found.push_back(fallback);
    } else {
      found = ResolveApiHost(fallback);
      if (found.size() > 8) found.clear();
      if (std::any_of(found.begin(), found.end(), [](const std::string& ip) { return !IsIpv4Literal(ip); }))
        found.clear();
    }
    for (const auto& ip : found)
      if (std::find(addresses.begin(), addresses.end(), ip) == addresses.end()) addresses.push_back(ip);
  }
  return addresses;
}

std::vector<std::string> TunnelController::ResolveApiHost(const std::string& host) {
  // ADR-196: while the block is on, a name is never sent to the system resolver (fail closed):
  // only addresses resolved before the block are used, and the rules already permit them.
  if (kill_switch_.engaged()) {
    auto it = known_api_.find(host);
    return it == known_api_.end() ? std::vector<std::string>{} : it->second;
  }
  std::vector<std::string> found = hooks_.resolve_ipv4(host);
  if (!found.empty()) known_api_[host] = found;
  return found;
}

void TunnelController::SetApiFallbacks(const std::vector<std::string>& hosts) {
  if (hosts.size() > 2) throw NativeError("native_config_invalid", kGenericMessage);
  std::set<std::string> seen;
  for (const auto& host : hosts)
    if (!(IsIpv4Literal(host) || IsLowerDnsName(host)) || !seen.insert(host).second)
      throw NativeError("native_config_invalid", kGenericMessage);
  std::lock_guard<std::mutex> lock(mutex_);
  api_fallback_hosts_ = hosts;
}

void TunnelController::Connect(const std::string& config, int64_t remaining_ms,
                               const std::optional<WindowGrant>& grant) {
  std::lock_guard<std::mutex> lock(mutex_);
  const int64_t submitted = hooks_.now_ms();
  const bool renewable = grant.has_value();
  auto state_error = []() { return NativeError("native_service_state_invalid", kGenericMessage); };
  if (remaining_ms <= 0 || remaining_ms > (renewable ? WindowDeadline::kMaxMs : 60000) || engine_started_ ||
      deadline_ != 0)
    throw state_error();
  if (renewable && (grant->connection.empty() || grant->runtime.empty())) throw state_error();
  if (!hooks_.is_admin()) throw NativeError("native_permission_error", kGenericMessage);

  const std::filesystem::path exe = hooks_.engine_dir / L"detour-engine.exe";
  if (!std::filesystem::is_regular_file(exe) || !std::filesystem::is_regular_file(hooks_.engine_dir / L"wintun.dll"))
    throw NativeError("native_engine_unavailable", kGenericMessage);

  std::string engine_config;
  RoutingPolicy policy;
  bool protect = false;
  try {
    Prefs prefs = LoadPrefs();
    protect = prefs.kill_switch;
    AwgProfile profile = ParseProfile(config, PrivateKey());
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
  // ADR-196: the block goes in before the engine starts, so there is no unprotected start-up.
  EngageForConnectLocked(protect, policy.api_ipv4);
  try {
    if (renewable) window_.Start(grant->connection, grant->runtime, grant->generation, deadline, hooks_.now_ms());
  } catch (const std::logic_error&) {
    throw state_error();
  }
  deadline_ = deadline;
  LaunchLocked(exe, engine_config);
  // ADR-194: kept only for an in-place restart of this tunnel; cleared by StopLocked.
  awg_config_ = config;
  awg_policy_ = std::move(policy);
  restartable_ = true;
}

void TunnelController::LaunchLocked(const std::filesystem::path& exe, std::string& engine_config, bool fresh) {
  reason_.clear();
  handshake_clock_ = HandshakeClock{};  // a new engine reports its own handshake values
  if (fresh) {
    handshake_seen_ = false;  // a new connection: nothing seen yet (kept after stop until here)
    stats_seen_ = false;
    restarts_in_place_ = 0;
    crash_restart_times_.clear();
    network_id_ = hooks_.physical_network ? hooks_.physical_network() : std::string();
    network_usable_ = !hooks_.physical_network || !network_id_.empty();
    next_network_poll_ = hooks_.now_ms() + hooks_.network_poll.count();
  }
  try {
    engine_.Start(exe.wstring(), L"run -c stdin", engine_config, true);
    engine_started_ = true;
  } catch (const std::exception&) {
    std::fill(engine_config.begin(), engine_config.end(), '\0');
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
    throw NativeError("native_service_state_invalid", kGenericMessage);
  }
  if (fresh) {
    watchdog_ = TunnelWatchdog{};  // a new connection carries no restart history
    watchdog_.TunnelUp(hooks_.now_ms());
  }
}

void TunnelController::ConnectVless(const VlessRealityProfile& profile, int64_t max_ms) {
  std::lock_guard<std::mutex> lock(mutex_);
  const int64_t submitted = hooks_.now_ms();
  if (max_ms <= 0 || max_ms > kMaxVlessMs || engine_started_ || deadline_ != 0)
    throw NativeError("native_service_state_invalid", kGenericMessage);
  if (!hooks_.is_admin()) throw NativeError("native_permission_error", kGenericMessage);

  const std::filesystem::path exe = hooks_.engine_dir / L"detour-engine.exe";
  if (!std::filesystem::is_regular_file(exe) || !std::filesystem::is_regular_file(hooks_.engine_dir / L"wintun.dll"))
    throw NativeError("native_engine_unavailable", kGenericMessage);

  std::string engine_config;
  bool protect = false;
  std::vector<std::string> api_ipv4;
  try {
    Prefs prefs = LoadPrefs();
    protect = prefs.kill_switch;
    RoutingPolicy policy;
    policy.mode = prefs.mode;
    policy.app_paths = prefs.apps;
    policy.domains = prefs.domains;
    policy.api_ipv4 = ControlPlaneAddresses();
    policy.dns_server = prefs.dns;
    api_ipv4 = policy.api_ipv4;
    engine_config = CreateVlessEngineConfig(profile, policy);
  } catch (const NativeError&) {
    throw;
  } catch (const std::invalid_argument&) {
    throw NativeError("native_config_invalid", kGenericMessage);
  } catch (const std::exception&) {
    throw NativeError("native_io_error", kGenericMessage);
  }
  EngageForConnectLocked(protect, api_ipv4);  // ADR-196, before the engine starts
  // No quota window: the panel meters its own client. The deadline only bounds one session.
  deadline_ = submitted + max_ms;
  LaunchLocked(exe, engine_config);
}

void TunnelController::AdvanceWindow(const WindowGrant& grant, int64_t remaining_ms) {
  std::lock_guard<std::mutex> lock(mutex_);
  const int64_t now = hooks_.now_ms();
  if (!engine_started_ || !engine_.Running() || remaining_ms <= 0 ||
      remaining_ms > WindowDeadline::kMaxMs)
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
    // Do not wait for the watcher: restart in place (ADR-194) or report the death together with
    // its reason, so the UI never sees "down" without a reason.
    EngineExitedLocked();
  }
  snapshot.up = engine_started_ && engine_.Running();
  snapshot.network = hooks_.network_available ? hooks_.network_available() : false;
  snapshot.remaining_ms = std::max<int64_t>(0, deadline_ - hooks_.now_ms());
  if (snapshot.up) {
    if (auto stats = engine_.Stats()) {
      const auto age = std::chrono::steady_clock::now() - stats->received_at;
      if (age < std::chrono::seconds(5)) {  // stale counters never confirm a handshake
        // W-06 (ADR-160): monotonic age; the wall clock is read only for a new handshake value.
        const int64_t handshake_age =
            handshake_clock_.AgeSeconds(stats->last_handshake_unix, hooks_.unix_seconds(), hooks_.now_ms());
        if (handshake_age >= 0) {
          snapshot.handshake_age = handshake_age;
          handshake_seen_ = true;
        }
        snapshot.traffic_bytes = stats->rx_bytes + stats->tx_bytes;
        snapshot.received_bytes = stats->rx_bytes;
      }
    }
  }
  snapshot.reason = reason_;
  snapshot.handshake_seen = handshake_seen_;
  return snapshot;
}

}  // namespace detour

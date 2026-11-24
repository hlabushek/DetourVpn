// Native owner of the Windows tunnel (the counterpart of ClientApplication.java). No Flutter types:
// tunnel_channel.cpp is the thin MethodChannel layer on top, so everything here is unit-testable.
// The private device key never leaves this class. Beta limits (ADR-095): no kill switch (when the
// engine dies the TUN disappears; ADR-196 is the unimplemented design), no Always-on. ADR-194: a
// watchdog restarts a crashed or stalled AWG engine in place and gives up to a new lease after a
// network change that two restarts did not recover.
#pragma once

#include <condition_variable>
#include <filesystem>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "config_factory.h"
#include "engine_process.h"
#include "handshake_clock.h"
#include "secure_vault.h"
#include "tunnel_watchdog.h"
#include "window_deadline.h"

namespace detour {

// A failure with the stable channel code and a Russian user-facing message.
struct NativeError : std::runtime_error {
  NativeError(std::string code_in, std::string message_ru)
      : std::runtime_error(code_in), code(std::move(code_in)), message(std::move(message_ru)) {}
  std::string code;
  std::string message;
};

struct WindowGrant {
  std::string connection;
  std::string runtime;
  int64_t generation = 0;
};

struct TunnelSnapshot {
  bool up = false;
  bool network = false;
  int64_t handshake_age = -1;  // seconds, -1 = none / stale
  int64_t remaining_ms = 0;
  std::optional<int64_t> traffic_bytes;
  std::optional<int64_t> received_bytes;
  std::string reason;
  // Task 7: a handshake was seen since the last connect; kept after the tunnel stops.
  bool handshake_seen = false;
};

struct ControllerHooks {
  std::filesystem::path vault_dir;
  std::filesystem::path prefs_path;
  std::filesystem::path engine_dir;  // contains detour-engine.exe and wintun.dll
  std::filesystem::path own_exe;     // never selectable as an excluded app
  std::function<bool()> is_admin;
  std::function<bool()> network_available;
  std::function<int64_t()> now_ms;  // monotonic
  std::function<int64_t()> unix_seconds;
  // Resolves a control-plane hostname to IPv4 literals (before the tunnel is up).
  std::function<std::vector<std::string>(const std::string&)> resolve_ipv4;
  std::chrono::milliseconds start_probe{1500};  // how long Connect waits to see the engine survive
  std::chrono::milliseconds stop_grace{10000};
  // ADR-194: identity of the usable physical network ("" = none). Compared in memory only, never
  // logged or sent anywhere. Unset = the watchdog treats the network as usable and never changed.
  std::function<std::string()> physical_network;
  std::chrono::milliseconds network_poll{2000};
  // ADR-194: in-place restarts after an engine crash allowed per crash_window.
  int crash_restarts = 2;
  std::chrono::milliseconds crash_window{600000};
};

class TunnelController {
 public:
  using Record = std::map<std::string, std::string>;

  explicit TunnelController(ControllerHooks hooks);
  ~TunnelController();

  static std::vector<std::string> Capabilities();

  bool Permission();
  std::string PublicKey();
  std::string Prove(const std::string& ephemeral_key, const std::string& transcript);

  // `grant` set = renewable window (<= WindowDeadline::kMaxMs, 3600 s), unset = plain connect (<= 60 s).
  void Connect(const std::string& config, int64_t remaining_ms, const std::optional<WindowGrant>& grant);
  // ADR-115: a panel server (VLESS + Reality). No window: the session ends at `max_ms`
  // (at most kMaxVlessMs) or on Disconnect. Same routing, DNS and error codes as Connect.
  static constexpr int64_t kMaxVlessMs = 12LL * 60 * 60 * 1000;
  void ConnectVless(const VlessRealityProfile& profile, int64_t max_ms);
  void AdvanceWindow(const WindowGrant& grant, int64_t remaining_ms);
  void Disconnect();
  TunnelSnapshot Snapshot();
  // ADR-194: the PC resumed from sleep or hibernation. Treated like a network change: a running
  // AWG tunnel must show a handshake newer than this or it is restarted in place.
  void SystemResumed();
  // ADR-194, test and diagnostics: in-place restarts done by the watchdog since the last connect.
  int RestartsInPlace();

  std::optional<Record> ReadSession();
  void WriteSession(const std::optional<Record>& value);
  std::optional<Record> ReadPendingLogin();
  void WritePendingLogin(const std::optional<Record>& value);
  std::optional<std::string> ReadNode();
  void WriteNode(const std::optional<std::string>& id);

  std::vector<std::string> SplitApps();
  std::vector<std::string> SplitDomains();
  std::string SplitMode();
  std::string SplitLastMode();
  // Returns the canonical domain list. Throws NativeError (domain_invalid, domain_duplicate, ...).
  std::vector<std::string> WriteSplit(const std::string& mode, const std::vector<std::string>& apps,
                                      const std::vector<std::string>& domains);
  // ADR-106: "" = profile DNS. WriteDns is allowed while connected (ADR-187 rule); applies at the
  // next connect, the running engine is not touched.
  std::string Dns();
  std::string WriteDns(const std::string& server);

 private:
  struct Prefs {
    std::string node, mode = "exclude", last_mode, dns;
    std::vector<std::string> apps, domains;
  };

  Prefs LoadPrefs() const;
  void SavePrefs(const Prefs& prefs) const;
  std::optional<Record> ReadRecord(const std::string& name, const std::vector<std::string>& fields);
  void WriteRecord(const std::string& name, const std::vector<std::string>& fields,
                   const std::optional<Record>& value);
  std::vector<std::string> ControlPlaneAddresses();
  void StopLocked();
  // Starts the engine with `engine_config` (wiped afterwards) and waits for the start probe.
  // `fresh` = a new connection (resets handshake memory and the watchdog); false = in place.
  void LaunchLocked(const std::filesystem::path& exe, std::string& engine_config, bool fresh = true);
  // ADR-194: the engine exited by itself. Restarts it in place within the crash budget, else stops.
  void EngineExitedLocked();
  // ADR-194: one watchdog tick for a running AWG tunnel (network poll, stale handshake).
  void WatchTickLocked();
  // ADR-194: rebuilds the engine config from the inputs kept at connect and relaunches it.
  // On failure the tunnel is stopped with `failure_reason`.
  bool RestartInPlaceLocked(const char* failure_reason);
  void WatchLoop();
  std::string PrivateKey();

  ControllerHooks hooks_;
  SecureVault vault_;
  std::mutex mutex_;
  EngineProcess engine_;
  WindowDeadline window_;
  int64_t deadline_ = 0;
  std::string reason_;
  std::optional<std::string> key_cache_;
  bool engine_started_ = false;
  bool handshake_seen_ = false;
  HandshakeClock handshake_clock_;

  // ADR-194: inputs of the running AWG tunnel, kept only to restart it in place. The Dart profile
  // holds no private key (the vault supplies it); the routing policy is the one built at connect,
  // so an in-place restart never applies rules saved later (ADR-187). Cleared on stop.
  std::string awg_config_;
  RoutingPolicy awg_policy_;
  bool restartable_ = false;
  TunnelWatchdog watchdog_;
  bool stats_seen_ = false;  // the engine reported peer stats on this connection
  bool network_usable_ = true;
  std::string network_id_;
  int64_t next_network_poll_ = 0;
  std::vector<int64_t> crash_restart_times_;
  int restarts_in_place_ = 0;

  std::condition_variable wake_;
  bool stopping_ = false;
  std::thread watcher_;
};

}  // namespace detour

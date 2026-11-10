// Native owner of the Windows tunnel (the counterpart of ClientApplication.java). No Flutter types:
// tunnel_channel.cpp is the thin MethodChannel layer on top, so everything here is unit-testable.
// The private device key never leaves this class. Beta limits (ADR-095): no kill switch (when the
// engine dies the TUN disappears), no Always-on.
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

#include "engine_process.h"
#include "secure_vault.h"
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

  // `grant` set = renewable window (<= 90 s), unset = plain connect (<= 60 s).
  void Connect(const std::string& config, int64_t remaining_ms, const std::optional<WindowGrant>& grant);
  void AdvanceWindow(const WindowGrant& grant, int64_t remaining_ms);
  void Disconnect();
  TunnelSnapshot Snapshot();

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

 private:
  struct Prefs {
    std::string node, mode = "exclude", last_mode;
    std::vector<std::string> apps, domains;
  };

  Prefs LoadPrefs() const;
  void SavePrefs(const Prefs& prefs) const;
  std::optional<Record> ReadRecord(const std::string& name, const std::vector<std::string>& fields);
  void WriteRecord(const std::string& name, const std::vector<std::string>& fields,
                   const std::optional<Record>& value);
  std::vector<std::string> ControlPlaneAddresses();
  void StopLocked();
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

  std::condition_variable wake_;
  bool stopping_ = false;
  std::thread watcher_;
};

}  // namespace detour

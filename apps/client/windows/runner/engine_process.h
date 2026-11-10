// Runs detour-engine.exe (Amnezia Box, ADR-095) as a child in a Job Object so it dies with the app.
// The config goes to the engine on stdin and is never written to disk or logged. Peer statistics
// arrive as "PEER_STATS {json}" lines on the private stdout pipe (windows-peer-stats-stdout.patch);
// no port is opened. The engine's own log output (stderr) is discarded.
#pragma once

#include "win_headers.h"

#include <atomic>
#include <chrono>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace detour {

struct PeerStats {
  int64_t last_handshake_unix = 0;  // 0 = no handshake yet
  int64_t rx_bytes = 0;
  int64_t tx_bytes = 0;
  std::chrono::steady_clock::time_point received_at;
};

// Parses `PEER_STATS {"peer_count":1,"last_handshake_time_sec":N,"rx_bytes":N,"tx_bytes":N}`.
// Anything else (including peer_count != 1) yields nullopt.
std::optional<PeerStats> ParsePeerStatsLine(const std::string& line);

class EngineProcess {
 public:
  EngineProcess() = default;
  ~EngineProcess();
  EngineProcess(const EngineProcess&) = delete;
  EngineProcess& operator=(const EngineProcess&) = delete;

  // Starts `exe` with `args` (command line after the exe name), writes `stdin_data` to its stdin
  // and closes it. Throws std::runtime_error("engine_start_failed") on failure.
  void Start(const std::wstring& exe, const std::wstring& args, const std::string& stdin_data,
             bool peer_stats);
  bool Running();
  std::optional<DWORD> ExitCode();
  std::optional<PeerStats> Stats();
  // Ctrl-C, wait up to `grace`, then terminate. Safe to call when not running.
  void Stop(std::chrono::milliseconds grace = std::chrono::seconds(10));
  DWORD ProcessId() const { return pid_; }

 private:
  void ReaderLoop(HANDLE pipe);
  void CloseHandles();
  bool SendCtrlC();

  HANDLE job_ = nullptr;
  HANDLE process_ = nullptr;
  DWORD pid_ = 0;
  std::thread reader_;
  std::mutex mutex_;
  std::optional<PeerStats> stats_;
};

}  // namespace detour

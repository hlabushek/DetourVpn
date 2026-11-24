// Port of WindowDeadline.java: pure monotonic v3 fencing. Replays cannot reset a deadline or revive
// a stopped VPN. Times are milliseconds on one monotonic clock chosen by the caller.
#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

namespace detour {

class WindowDeadline {
 public:
  // ADR-150: the longest window the server and nodes grant (3600 s). Same value as
  // WindowDeadline.MAX_MS on Android and WindowConnectionController.maxWindowMs in Dart.
  static constexpr int64_t kMaxMs = 3600000;

  std::string connection;
  std::string runtime;
  int64_t generation = 0;
  int64_t deadline = 0;

  // Throws std::logic_error("window_state_invalid") on any violation.
  void Start(const std::string& id, const std::string& epoch, int64_t version, int64_t end, int64_t now) {
    if (!connection.empty() || version != 1 || end <= now || end - now > kMaxMs) Fail();
    connection = id;
    runtime = epoch;
    generation = version;
    deadline = end;
  }
  void Advance(const std::string& id, const std::string& epoch, int64_t version, int64_t end, int64_t now) {
    if (connection.empty() || connection != id || runtime != epoch || now >= deadline || end <= now ||
        end - now > kMaxMs)
      Fail();
    if (version == generation) return;
    if (version != generation + 1 || end <= deadline) Fail();
    generation = version;
    deadline = end;
  }
  void Clear() {
    connection.clear();
    runtime.clear();
    generation = 0;
    deadline = 0;
  }

 private:
  [[noreturn]] static void Fail() { throw std::logic_error("window_state_invalid"); }
};

}  // namespace detour

// Port of TunnelWatchdog.java (ADR-099/122/146) for the Windows controller (ADR-194). Pure policy,
// no I/O: when to restart a running tunnel in place and when to give up and ask for a new lease.
// Inputs are the caller's monotonic milliseconds and the handshake age in milliseconds (-1 = no
// handshake yet). Same constants and rules as the Android class; keep both in step.
// Not thread-safe: the caller holds its own lock.
#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>

namespace detour {

class TunnelWatchdog {
 public:
  // Keepalive 25 s plus WireGuard rekey every 120 s: 180 s without a handshake is a stall.
  static constexpr int64_t kStaleMs = 180000;
  static constexpr int64_t kFreshMs = 60000;
  static constexpr int64_t kSettleMs = 500;
  static constexpr int64_t kNetworkGapMs = 15000;
  static constexpr int64_t kStaleGapMs = 120000;
  static constexpr int64_t kMaxGapMs = 600000;
  // Restarts per network change without a handshake, then a new lease.
  static constexpr int kNetworkRestarts = 2;

  enum class Action { kNone, kRestart, kNewLease };

  void TunnelUp(int64_t now) {
    up_since_ = now;
    attempts_ = 0;
    network_changed_at_ = -1;
    network_restarts_ = 0;
  }

  void TunnelDown() {
    up_since_ = -1;
    network_changed_at_ = -1;
    network_restarts_ = 0;
  }

  bool Running() const { return up_since_ >= 0; }

  // The physical network changed (or the PC resumed from sleep). Ignored while no tunnel runs.
  void NetworkChanged(int64_t now) {
    if (up_since_ < 0) return;
    network_changed_at_ = now;
    network_restarts_ = 0;
    attempts_ = 0;
  }

  bool NetworkPending() const { return up_since_ >= 0 && network_changed_at_ >= 0; }

  int64_t StaleGapMs() const {
    return std::min<int64_t>(kMaxGapMs, kStaleGapMs << std::min(attempts_, 3));
  }

  // What the caller should do now. After kRestart it reports Restarted(); after kNewLease it
  // stops the tunnel and reports TunnelDown().
  Action Decide(int64_t now, int64_t handshake_age_ms, bool usable_network) {
    if (up_since_ < 0 || !usable_network) return Action::kNone;  // a pending change waits
    if (handshake_age_ms >= 0 && handshake_age_ms <= kFreshMs) attempts_ = 0;
    if (network_changed_at_ >= 0 && now - network_changed_at_ >= kSettleMs) {
      const int64_t since_change = now - network_changed_at_;
      const bool handshake_after_change = handshake_age_ms >= 0 && handshake_age_ms < since_change;
      if (handshake_after_change) {
        network_changed_at_ = -1;
        network_restarts_ = 0;
      } else if (now - last_restart_ >= kNetworkGapMs) {
        if (network_restarts_ >= kNetworkRestarts) {
          network_changed_at_ = -1;
          network_restarts_ = 0;
          return Action::kNewLease;
        }
        last_was_network_ = true;
        return Action::kRestart;
      }
      // Otherwise it stays pending until the gap since the last restart has passed.
    }
    const bool stale = handshake_age_ms < 0 ? now - up_since_ >= kStaleMs : handshake_age_ms >= kStaleMs;
    if (stale && now - up_since_ >= kStaleMs && now - last_restart_ >= StaleGapMs()) {
      last_was_network_ = false;
      return Action::kRestart;
    }
    return Action::kNone;
  }

  void Restarted(int64_t now) {
    last_restart_ = now;
    if (last_was_network_) {
      // Verify this restart: if no handshake follows it, restart again or ask for a lease.
      network_restarts_++;
      network_changed_at_ = now;
    } else {
      attempts_ = std::min(attempts_ + 1, 8);
    }
    last_was_network_ = false;
  }

 private:
  int64_t up_since_ = -1;
  int64_t last_restart_ = std::numeric_limits<int64_t>::min() / 2;
  int64_t network_changed_at_ = -1;
  int network_restarts_ = 0;
  bool last_was_network_ = false;
  int attempts_ = 0;
};

}  // namespace detour

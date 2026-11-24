// Port of HandshakeClock.java (ADR-160, W-06): turns the engine's last-handshake time (Unix
// wall-clock seconds) into a monotonic age. The wall clock is used once, at the first observation
// of each new handshake value; that age is anchored to the monotonic clock and every later age is
// a monotonic delta, so a wall-clock step (manual change, NTP correction) cannot make a handshake
// look older or vanish. A value in the future at its first observation has an unknown age (-1);
// it resolves at the next handshake. Not thread-safe: the caller holds its own lock.
#pragma once

#include <cstdint>
#include <limits>

namespace detour {

class HandshakeClock {
 public:
  // handshake_unix: the engine value (<= 0 = no handshake yet); wall_now_s: Unix seconds now (used
  // only for a new value); mono_now_ms: the caller's monotonic clock. Returns seconds, -1 = none.
  int64_t AgeSeconds(int64_t handshake_unix, int64_t wall_now_s, int64_t mono_now_ms) {
    if (handshake_unix <= 0) {
      seen_ = 0;
      at_mono_ms_ = kUnknown;
      return -1;
    }
    if (handshake_unix != seen_) {
      seen_ = handshake_unix;
      const int64_t age_s = wall_now_s - handshake_unix;
      at_mono_ms_ = age_s < 0 ? kUnknown : mono_now_ms - age_s * 1000;
    }
    if (at_mono_ms_ == kUnknown) return -1;
    const int64_t age_ms = mono_now_ms - at_mono_ms_;
    return age_ms < 0 ? 0 : age_ms / 1000;
  }

 private:
  static constexpr int64_t kUnknown = std::numeric_limits<int64_t>::min();
  int64_t seen_ = 0;
  int64_t at_mono_ms_ = kUnknown;
};

}  // namespace detour

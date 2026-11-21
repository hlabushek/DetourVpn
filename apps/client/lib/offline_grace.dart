import 'dart:math';

/// ADR-162: the bounded time a running lease may be used while its renewal cannot reach the API.
///
/// Pure and clock-free: callers pass monotonic milliseconds (the controller's Stopwatch). It never
/// extends a lease. The grace starts when a renewal first fails (transport failure on every API
/// origin) and ends at the earlier of [length] after that and the lease's own deadline. A
/// successful API answer clears it.
class OfflineGrace {
  OfflineGrace({this.length = defaultLength});

  static const defaultLength = Duration(hours: 1);

  /// How long after the first renewal failure the lease may still be used.
  final Duration length;

  int? _failedAtMs;

  /// Monotonic time at which renewal first failed in the current streak; null when none.
  int? get failedAtMs => _failedAtMs;

  /// A renewal failed. Only the first failure of a streak starts the grace.
  void renewalFailed(int nowMs) => _failedAtMs ??= nowMs;

  /// The API answered: the streak (and the grace) ends.
  void clear() => _failedAtMs = null;

  bool get active => _failedAtMs != null;

  /// When the grace ends: the earlier of [length] after the first failure and [deadlineMs] (the
  /// lease's own monotonic deadline). Null when no renewal failed.
  int? endMs(int deadlineMs) {
    final failed = _failedAtMs;
    if (failed == null) return null;
    return min(failed + length.inMilliseconds, deadlineMs);
  }

  /// True once the grace is over at [nowMs]; false while no renewal failed.
  bool ended(int nowMs, int deadlineMs) {
    final end = endMs(deadlineMs);
    return end != null && nowMs >= end;
  }

  /// Milliseconds of grace left at [nowMs] (0 when over); null when no renewal failed.
  int? remainingMs(int nowMs, int deadlineMs) {
    final end = endMs(deadlineMs);
    return end == null ? null : max(0, end - nowMs);
  }
}

/// ADR-162: Russian status text for a lease running on offline grace.
String offlineGraceText(int remainingMs) {
  final minutes = (remainingMs / 60000).ceil();
  return 'Нет связи с сервером. VPN работает по текущему доступу ещё '
      '${minutes < 1 ? 1 : minutes} мин';
}

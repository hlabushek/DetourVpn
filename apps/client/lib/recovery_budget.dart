/// ADR-139: a bounded budget for automatic recovery. Pure Dart (no timers, no platform types),
/// so the rule is unit-tested without a clock.
///
/// One failure streak gets [maxAttempts] automatic attempts. Each attempt waits
/// `base * multiplier`, with the multipliers below (2 s, 5 s, 15 s, 30 s, 60 s with the default
/// base). When the budget is spent [take] returns null: the caller must end in a terminal failed
/// state, not keep retrying. A successful window and a user tap call [reset].
class RecoveryBudget {
  RecoveryBudget({this.maxAttempts = defaultMaxAttempts});

  /// Five automatic attempts in a streak; the previous code had no limit.
  static const defaultMaxAttempts = 5;

  /// Delay multipliers by attempt number (the last one is reused only if [maxAttempts] is larger).
  static const multipliers = [1.0, 2.5, 7.5, 15.0, 30.0];

  final int maxAttempts;
  int _used = 0;

  /// Automatic attempts used in the current streak.
  int get used => _used;

  /// Attempts left before the terminal state.
  int get remaining => maxAttempts - _used < 0 ? 0 : maxAttempts - _used;

  bool get exhausted => _used >= maxAttempts;

  /// Spends one attempt. Returns the delay multiplier for it, or null when the budget is spent.
  double? take() {
    if (exhausted) return null;
    final multiplier =
        multipliers[_used < multipliers.length
            ? _used
            : multipliers.length - 1];
    _used++;
    return multiplier;
  }

  /// A successful window or an explicit user tap starts a fresh streak.
  void reset() => _used = 0;
}

/// ADR-139/140: the pace of automatic recovery. Pure Dart (no timers, no platform types), so the
/// rule is unit-tested without a clock.
///
/// A failure streak starts with [fastAttempts] quick attempts, each waiting `base * multiplier`
/// (2 s, 5 s, 15 s, 30 s, 60 s with the default base). ADR-140: after that it keeps retrying at the
/// last step (60 s) for as long as the user wants the VPN; [take] never ends the streak. Only a
/// terminal cause, which the caller decides, stops the retries. A successful window and a user tap
/// call [reset].
class RecoveryBudget {
  RecoveryBudget({this.fastAttempts = defaultFastAttempts})
    : assert(fastAttempts >= 1);

  /// Quick attempts at the start of a streak (ADR-139 kept the number, ADR-140 removed the end).
  static const defaultFastAttempts = 5;

  /// Delay multipliers by attempt number; the last one is reused for every later attempt.
  static const multipliers = [1.0, 2.5, 7.5, 15.0, 30.0];

  final int fastAttempts;
  int _used = 0;

  /// Automatic attempts made in the current streak.
  int get used => _used;

  /// The quick steps are spent; attempts continue at the slow pace.
  bool get slow => _used >= fastAttempts;

  /// Spends one attempt and returns its delay multiplier. Never null: a wanted connection keeps
  /// retrying (ADR-140).
  double take() {
    final last =
        (fastAttempts < multipliers.length
            ? fastAttempts
            : multipliers.length) -
        1;
    final index = _used < last ? _used : last;
    _used++;
    return multipliers[index];
  }

  /// A successful window or an explicit user tap starts a fresh streak.
  void reset() => _used = 0;
}

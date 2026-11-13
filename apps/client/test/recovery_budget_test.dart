import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/recovery_budget.dart';

/// ADR-139/140: the automatic recovery pace is deterministic and never ends a wanted connection.
void main() {
  test('ADR-140: quick steps first, then the slow step forever', () {
    final budget = RecoveryBudget(fastAttempts: 3);
    expect(budget.take(), 1.0);
    expect(budget.take(), 2.5);
    expect(budget.take(), 7.5);
    expect(budget.slow, isTrue);
    for (var i = 0; i < 100; i++) {
      expect(budget.take(), 7.5, reason: 'never ends, never grows');
    }
    expect(budget.used, 103);
  });

  test('the default streak is five quick steps then 30x the base', () {
    final budget = RecoveryBudget();
    expect(RecoveryBudget.defaultFastAttempts, 5);
    final steps = [for (var i = 0; i < 5; i++) budget.take()];
    expect(steps, [1.0, 2.5, 7.5, 15.0, 30.0]);
    expect(budget.slow, isTrue);
    expect(budget.take(), 30.0);
  });

  test('reset starts a fresh streak (successful window or user tap)', () {
    final budget = RecoveryBudget(fastAttempts: 2);
    budget.take();
    budget.take();
    expect(budget.slow, isTrue);
    budget.reset();
    expect(budget.slow, isFalse);
    expect(budget.used, 0);
    expect(budget.take(), 1.0);
  });

  test('more quick steps than multipliers reuse the last multiplier', () {
    final budget = RecoveryBudget(fastAttempts: 8);
    final steps = [for (var i = 0; i < 12; i++) budget.take()];
    expect(steps.last, 30.0);
    expect(steps.every((s) => s <= 30.0), isTrue);
  });

  test('recovery_exhausted has its own message with one action', () {
    final text = messageFor('recovery_exhausted');
    expect(text, contains('«Подключить»'));
    expect(text, isNot(messageFor('some_unknown_code')));
  });

  test('Always-on hint: path when off, Lockdown warning, silent otherwise', () {
    expect(alwaysOnHint('off'), contains('Настройки → Сеть и интернет → VPN'));
    expect(
      alwaysOnHint('lockdown'),
      contains('Блокировать соединения без VPN'),
    );
    expect(alwaysOnHint('on'), isNull);
    expect(alwaysOnHint('unknown'), isNull);
    expect(alwaysOnHint(null), isNull);
  });
}

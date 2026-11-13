import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/recovery_budget.dart';

/// ADR-139: the automatic recovery budget is finite and deterministic.
void main() {
  test('one streak gets exactly maxAttempts automatic attempts', () {
    final budget = RecoveryBudget(maxAttempts: 3);
    expect(budget.take(), 1.0);
    expect(budget.take(), 2.5);
    expect(budget.take(), 7.5);
    expect(budget.exhausted, isTrue);
    expect(budget.take(), isNull, reason: 'the terminal state, not a retry');
    expect(budget.take(), isNull);
    expect(budget.used, 3);
    expect(budget.remaining, 0);
  });

  test('the default budget is five attempts with bounded delays', () {
    final budget = RecoveryBudget();
    expect(RecoveryBudget.defaultMaxAttempts, 5);
    final steps = [for (var i = 0; i < 5; i++) budget.take()];
    expect(steps, [1.0, 2.5, 7.5, 15.0, 30.0]);
    expect(budget.take(), isNull);
  });

  test('reset starts a fresh streak (successful window or user tap)', () {
    final budget = RecoveryBudget(maxAttempts: 2);
    budget.take();
    budget.take();
    expect(budget.exhausted, isTrue);
    budget.reset();
    expect(budget.exhausted, isFalse);
    expect(budget.remaining, 2);
    expect(budget.take(), 1.0);
  });

  test('a long streak keeps the last multiplier and never grows', () {
    final budget = RecoveryBudget(maxAttempts: 8);
    final steps = [for (var i = 0; i < 8; i++) budget.take()];
    expect(steps.last, 30.0);
    expect(steps.every((s) => s != null && s <= 30.0), isTrue);
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

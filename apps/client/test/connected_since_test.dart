import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connected_since.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/connection_status.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

const a = '11111111-1111-4111-8111-111111111111';
const b = '22222222-2222-4222-8222-222222222222';

/// MOCK: in-memory stand-in for the native preference; no device storage.
class MemoryStore implements ConnectedSinceStore {
  MemoryStore([this.value]);
  ConnectedSince? value;
  int writes = 0;
  @override
  Future<ConnectedSince?> readConnectedSince() async => value;
  @override
  Future<void> writeConnectedSince(ConnectedSince? next) async {
    writes++;
    value = next;
  }
}

void main() {
  group('timer text', () {
    test('hours, minutes and seconds with leading zeros', () {
      expect(connectedDurationText(0), '00:00:00');
      expect(connectedDurationText(8075000), '02:14:35');
      expect(connectedDurationText(999), '00:00:00');
      expect(connectedDurationText(360000000), '100:00:00');
      expect(connectedDurationText(-1000), '00:00:00');
    });
    test('«Подключено» line, none without a start', () {
      expect(connectedTimerText(1000, 8076000), 'Подключено 02:14:35');
      expect(connectedTimerText(null, 8076000), isNull);
    });
  });

  group('ConnectedSinceTracker', () {
    test('starts on the first live id and stores it', () async {
      final store = MemoryStore();
      final t = ConnectedSinceTracker(store, nowMs: () => 5000);
      expect(await t.observe(a), isTrue);
      expect(t.sinceMs, 5000);
      expect(store.value?.id, a);
      expect(await t.observe(a), isFalse);
    });

    test(
      'resumes the stored start of the same connection after a restart',
      () async {
        final store = MemoryStore(const ConnectedSince(a, 1000));
        final t = ConnectedSinceTracker(store, nowMs: () => 9000);
        // App start: not live yet; the stored record survives until adoption.
        expect(await t.observe(null), isFalse);
        expect(store.value?.sinceMs, 1000);
        await t.observe(a);
        expect(t.sinceMs, 1000);
        expect(store.writes, 0);
      },
    );

    test('a different connection starts a new timer', () async {
      final store = MemoryStore(const ConnectedSince(a, 1000));
      final t = ConnectedSinceTracker(store, nowMs: () => 9000);
      await t.observe(b);
      expect(t.sinceMs, 9000);
      expect(store.value?.id, b);
    });

    test('disconnect clears memory and storage', () async {
      final store = MemoryStore();
      final t = ConnectedSinceTracker(store, nowMs: () => 5000);
      await t.observe(a);
      expect(await t.observe(null), isTrue);
      expect(t.sinceMs, isNull);
      expect(store.value, isNull);
    });

    test('no store or a failing store keeps the timer in memory', () async {
      final t = ConnectedSinceTracker(_FailingStore(), nowMs: () => 7000);
      await t.observe(a);
      expect(t.sinceMs, 7000);
      final plain = ConnectedSinceTracker(null, nowMs: () => 7000);
      await plain.observe('panel');
      expect(plain.sinceMs, 7000);
    });
  });

  testWidgets('home status shows the timer only while connected', (
    tester,
  ) async {
    final c = _TimerController(DateTime.now().millisecondsSinceEpoch - 8075000);
    addTearDown(c.dispose);
    Future<void> pump() => tester.pumpWidget(
      MaterialApp(home: ConnectionLiveStatus(controller: c)),
    );
    await pump();
    expect(find.textContaining('Подключено'), findsNothing);
    c.phase = ConnectionPhase.connected;
    await pump();
    expect(find.text('Подключено 02:14:35'), findsOneWidget);
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpWidget(const SizedBox());
  });
}

/// MOCK: storage that always fails, as on a platform without the channel method.
class _FailingStore implements ConnectedSinceStore {
  @override
  Future<ConnectedSince?> readConnectedSince() => Future.error(StateError('x'));
  @override
  Future<void> writeConnectedSince(ConnectedSince? value) =>
      Future.error(StateError('x'));
}

/// TEST ONLY: fixed start time; no network, no tunnel.
class _TimerController extends ConnectionController {
  _TimerController(this.since) : super(FakeApi(), FakeNative());
  final int since;
  @override
  int? get connectedSinceMs => since;
}

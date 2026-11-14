import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/connection_status.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

void main() {
  group('shortDurationText', () {
    test('seconds, minutes and hours', () {
      expect(shortDurationText(0), '0 с');
      expect(shortDurationText(12), '12 с');
      expect(shortDurationText(60), '1 мин');
      expect(shortDurationText(185), '3 мин 5 с');
      expect(shortDurationText(3600), '1 ч');
      expect(shortDurationText(3720), '1 ч 2 мин');
      expect(shortDurationText(-5), '0 с');
    });
  });

  group('handshakeStatusText', () {
    test('age in plain Russian, advanced by the time since it was read', () {
      expect(handshakeStatusText(12), 'Последний handshake 12 с назад');
      expect(
        handshakeStatusText(12, elapsedMs: 3500),
        'Последний handshake 15 с назад',
      );
      expect(handshakeStatusText(-1), 'Handshake ещё не получен');
      expect(handshakeStatusText(null), isNull);
    });
  });

  group('renewalStatusText', () {
    test('time until renewal starts, rounded up', () {
      expect(renewalStatusText(95000, 55000), 'Следующее продление через 40 с');
      expect(
        renewalStatusText(95000, 55000, elapsedMs: 10500),
        'Следующее продление через 30 с',
      );
      expect(
        renewalStatusText(900000, 300000),
        'Следующее продление через 10 мин',
      );
    });
    test('renewal under way, window over, or unknown', () {
      expect(renewalStatusText(50000, 55000), 'Идёт продление окна');
      expect(renewalStatusText(0, 55000), isNull);
      expect(renewalStatusText(95000, 55000, elapsedMs: 96000), isNull);
      expect(renewalStatusText(null, 55000), isNull);
      expect(renewalStatusText(95000, null), isNull);
    });
  });

  test('live status is shown only while connected or reconnecting', () {
    expect(showsLiveStatus(ConnectionPhase.connected), isTrue);
    expect(showsLiveStatus(ConnectionPhase.reconnecting), isTrue);
    expect(showsLiveStatus(ConnectionPhase.disconnected), isFalse);
    expect(showsLiveStatus(ConnectionPhase.connecting), isFalse);
    expect(showsLiveStatus(ConnectionPhase.failed), isFalse);
  });

  testWidgets('status line hides when disconnected', (tester) async {
    final c = _StatusController();
    addTearDown(c.dispose);
    c.statusHandshakeAgeSec = 12;
    c.statusRemainingMs = 95000;
    c.statusSampledAt = DateTime.now();
    await tester.pumpWidget(
      MaterialApp(home: ConnectionLiveStatus(controller: c)),
    );
    expect(find.textContaining('handshake'), findsNothing);
    c.phase = ConnectionPhase.connected;
    await tester.pumpWidget(
      MaterialApp(home: ConnectionLiveStatus(controller: c)),
    );
    expect(find.textContaining('Последний handshake 12 с назад'), findsOne);
    expect(find.textContaining('Следующее продление через 40 с'), findsOne);
    await tester.pumpWidget(const SizedBox());
  });
}

/// TEST ONLY: a controller with a fixed renewal lead; no network, no tunnel.
class _StatusController extends ConnectionController {
  _StatusController() : super(FakeApi(), FakeNative());
  @override
  int? get renewLeadMs => 55000;
}

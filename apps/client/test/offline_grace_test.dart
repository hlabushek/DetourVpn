import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/home_screen.dart';
import 'package:vpn_client/offline_grace.dart';
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/window_connection.dart';

import 'window_connection_test.dart' show StateNative, WindowsApi, node;

// TEST ONLY: simulated API and native (ADR-162). No real tunnel, node or network.

/// Every API call fails with a transport error while [offline] is set (all origins down).
class OfflineApi implements TunnelApi {
  OfflineApi(this.inner);
  final WindowsApi inner;
  bool offline = false;
  final calls = <String>[];
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) {
    calls.add('$method $path');
    if (offline) throw const ClientError('network_unavailable');
    return inner.call(method, path, body);
  }
}

void main() {
  group('OfflineGrace (pure)', () {
    test('ends one hour after the first renewal failure', () {
      final grace = OfflineGrace();
      expect(grace.active, false);
      expect(grace.endMs(10 * 3600000), isNull);
      expect(grace.ended(10 * 3600000, 10 * 3600000), false);
      grace.renewalFailed(1000);
      grace.renewalFailed(500000); // later failures do not move the start
      expect(grace.failedAtMs, 1000);
      const longLease = 1000 + 2 * 3600000;
      expect(grace.endMs(longLease), 1000 + 3600000);
      expect(grace.ended(1000 + 3600000 - 1, longLease), false);
      expect(grace.ended(1000 + 3600000, longLease), true);
      expect(grace.remainingMs(1000 + 1800000, longLease), 1800000);
    });

    test('the earlier of one hour and the lease expiry wins', () {
      final grace = OfflineGrace()..renewalFailed(0);
      // The lease expires before the hour: the lease's own deadline ends the grace.
      expect(grace.endMs(600000), 600000);
      expect(grace.ended(600000, 600000), true);
      expect(grace.ended(599999, 600000), false);
      // The lease would outlive the hour: the hour ends it.
      expect(grace.endMs(5 * 3600000), 3600000);
    });

    test('a successful answer clears the streak', () {
      final grace = OfflineGrace()..renewalFailed(10);
      grace.clear();
      expect(grace.active, false);
      grace.renewalFailed(900);
      expect(grace.failedAtMs, 900);
    });

    test('the status text names the minutes left, at least one', () {
      expect(offlineGraceText(30 * 60000), contains('ещё 30 мин'));
      expect(offlineGraceText(1), contains('ещё 1 мин'));
      expect(offlineGraceText(0), contains('ещё 1 мин'));
      expect(offlineGraceText(90000), startsWith('Нет связи с сервером'));
    });
  });

  group('WindowConnectionController offline grace', () {
    late WindowsApi inner;
    late OfflineApi api;
    late StateNative state;
    WindowConnectionController android({Duration? grace}) {
      final value = WindowConnectionController(
        api,
        state,
        pollInterval: const Duration(days: 1),
        // 90 s windows with renewal due at once: every poll renews.
        renewBeforeMs: 90000,
        autoReconnectDelay: null,
        nativeOwnsBackgroundRecovery: true,
        offlineGrace: grace ?? OfflineGrace.defaultLength,
      )..selectedNode = node;
      addTearDown(value.dispose);
      return value;
    }

    setUp(() {
      inner = WindowsApi();
      api = OfflineApi(inner);
      state = StateNative()
        ..age = 0
        ..restoreActive = false;
    });

    test(
      'a failed renewal keeps the tunnel and shows a degraded state',
      () async {
        final ui = android();
        await ui.connect();
        await ui.refresh();
        expect(ui.phase, ConnectionPhase.connected);
        expect(ui.offlineGrace, false);
        final stops = state.stops;
        api.offline = true;
        await ui.refresh();
        expect(ui.offlineGrace, true);
        expect(ui.phase, ConnectionPhase.connected);
        expect(ui.error, isNull);
        expect(ui.detail, startsWith('Нет связи с сервером'));
        expect(
          state.stops,
          stops,
          reason: 'renewal failure never drops the tunnel',
        );
        expect(ui.hasLease, true);
        // The grace never outlives the lease (90 s window here, far below one hour).
        expect(
          ui.offlineGraceRemaining!.inMilliseconds,
          lessThanOrEqualTo(90000),
        );
        // The API is back: the degraded state ends.
        api.offline = false;
        await ui.refresh();
        expect(ui.offlineGrace, false);
        expect(ui.detail, isNot(startsWith('Нет связи с сервером')));
      },
    );

    test('when the grace ends the tunnel is disconnected', () async {
      final ui = android(grace: const Duration(milliseconds: 50));
      await ui.connect();
      await ui.refresh();
      api.offline = true;
      await ui.refresh();
      expect(ui.offlineGrace, true);
      final stops = state.stops;
      await Future<void>.delayed(const Duration(milliseconds: 80));
      await ui.refresh();
      expect(
        state.stops,
        greaterThan(stops),
        reason: 'grace over: native stopped',
      );
      expect(state.up, false);
      expect(ui.phase, ConnectionPhase.failed);
      expect(ui.error, 'lease_expired');
      expect(ui.offlineGrace, false);
    });

    test(
      'a dropped tunnel restarts on its still-valid lease while offline',
      () async {
        final ui = android();
        await ui.connect();
        await ui.refresh();
        final creates = inner.creates;
        final starts = state.starts;
        api.offline = true;
        await ui.refresh(); // renewal fails: grace starts
        expect(ui.offlineGrace, true);
        state.up = false; // the service was killed; time is left on the lease
        await ui.refresh();
        expect(
          state.starts,
          starts + 1,
          reason: 'the same lease, started again',
        );
        expect(state.up, true);
        expect(
          inner.creates,
          creates,
          reason: 'no new lease, no API call worked',
        );
        expect(inner.deletes, 0, reason: 'the held lease is not revoked');
        expect(ui.hasLease, true);
        expect(ui.phase, isNot(ConnectionPhase.failed));
        // The native deadline is never later than the lease's.
        expect(state.windowMs, lessThanOrEqualTo(90000));
      },
    );

    test(
      'no offline resume without a valid lease or with the API reachable',
      () async {
        final ui = android();
        await ui.connect();
        await ui.refresh();
        final starts = state.starts;
        state.up =
            false; // the API is reachable: the normal path (revoke, new lease) runs
        await ui.refresh();
        expect(state.starts, starts);
        expect(inner.deletes, 1);
      },
    );

    test('offline resumes are bounded per streak', () async {
      final ui = android();
      await ui.connect();
      await ui.refresh();
      api.offline = true;
      await ui.refresh();
      final starts = state.starts;
      for (
        var i = 0;
        i < WindowConnectionController.maxOfflineResumes + 2;
        i++
      ) {
        state.up = false;
        await ui.refresh();
      }
      expect(
        state.starts - starts,
        WindowConnectionController.maxOfflineResumes,
      );
      expect(ui.phase, ConnectionPhase.failed);
    });

    testWidgets('the home screen shows the degraded state', (tester) async {
      final ui = android();
      await tester.runAsync(() async {
        await ui.connect();
        await ui.refresh();
        api.offline = true;
        await ui.refresh();
      });
      expect(ui.offlineGrace, true);
      await tester.pumpWidget(
        MaterialApp(
          theme: orbitTheme(Brightness.dark),
          home: Scaffold(
            body: ConnectionScreen(controller: ui, onSignOut: () async {}),
          ),
        ),
      );
      await tester.pump();
      expect(find.textContaining('Нет связи с сервером'), findsOneWidget);
      expect(find.text('VPN активен на этом устройстве'), findsNothing);
      ui.pausePolling();
      await tester.pumpWidget(const SizedBox());
    });

    test('a brand-new connect is not possible offline', () async {
      final ui = android();
      api.offline = true;
      await ui.connect();
      expect(state.starts, 0);
      expect(ui.hasLease, false);
      expect(ui.phase, ConnectionPhase.failed);
    });
  });
}

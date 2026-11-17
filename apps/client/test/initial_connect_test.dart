// ADR-145 (Phase 2): initial connect hardening. TEST ONLY simulated API and native; no Android,
// node, DNS or egress evidence.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/diagnostic_report.dart';
import 'package:vpn_client/home_screen.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/window_connection.dart';

import 'window_connection_test.dart' show IntentNative, WindowsApi, node;

/// TEST ONLY: records how many API calls ran when consent was asked, and can fail snapshots.
class ConsentNative extends IntentNative {
  ConsentNative(this.api);
  final WindowsApi api;
  final apiCallsAtConsent = <int>[];
  int failSnapshots = 0;
  int snapshotCalls = 0;

  @override
  Future<bool> permission() async {
    apiCallsAtConsent.add(api.calls.length);
    order.add('permission');
    return super.permission();
  }

  @override
  Future<NativeSnapshot> snapshot() async {
    snapshotCalls++;
    if (failSnapshots > 0) {
      failSnapshots--;
      throw StateError('TEST ONLY snapshot failure');
    }
    return super.snapshot();
  }
}

/// TEST ONLY: serves the node list (or fails it) and delegates everything else.
class NodeListApi implements TunnelApi {
  NodeListApi(this.inner);
  final WindowsApi inner;
  bool failNodes = false;
  int nodeLists = 0;
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (path == '${WindowConnectionController.root}/nodes?include=panel') {
      nodeLists++;
      if (failNodes) throw const ClientError('network_unavailable');
      return {
        'nodes': [
          {
            'id': node,
            'name': 'TEST ONLY',
            'region': 'test',
            'available': true,
          },
        ],
        'checked_at': DateTime.now().toUtc().toIso8601String(),
      };
    }
    return inner.call(method, path, body);
  }
}

bool _apiWork(String call) => !call.contains('/quota');

void main() {
  late WindowsApi api;
  late ConsentNative native;

  WindowConnectionController controller({
    TunnelApi? using,
    Duration handshakeTimeout = const Duration(seconds: 20),
  }) {
    final value = WindowConnectionController(
      using ?? api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: const Duration(milliseconds: 5),
      snapshotRetryDelay: const Duration(milliseconds: 1),
      handshakeTimeout: handshakeTimeout,
    );
    addTearDown(value.dispose);
    return value;
  }

  setUp(() {
    api = WindowsApi();
    native = ConsentNative(api)..age = 0;
  });

  group('2.1 consent before any API call', () {
    test('VPN consent is asked before device registration', () async {
      final c = controller()..selectedNode = node;
      await c.connect();
      expect(c.error, isNull);
      expect(native.apiCallsAtConsent, [0]);
      expect(api.calls.first, 'POST /api/v1/devices');
    });

    test('a refused consent makes no API call at all', () async {
      native.allowed = false;
      final c = controller()..selectedNode = node;
      await c.connect();
      expect(c.error, 'permission_denied');
      expect(api.calls.where(_apiWork), isEmpty);
      expect(api.deviceBound, isFalse);
      expect(api.creates, 0);
    });
  });

  group('2.2 a failed snapshot during reconcile is unknown', () {
    test(
      'a snapshot that fails once is retried and the connect goes on',
      () async {
        native.failSnapshots = 1;
        final c = controller()..selectedNode = node;
        await c.connect();
        expect(c.error, isNull);
        expect(c.hasLease, isTrue);
        expect(native.snapshotCalls, greaterThanOrEqualTo(2));
      },
    );

    test(
      'a snapshot that keeps failing stops nothing and keeps the intent',
      () async {
        native
          ..up = true
          ..failSnapshots = 1000;
        final c = controller()..selectedNode = node;
        await c.connect();
        expect(c.error, 'native_state_unknown');
        expect(native.stops, 0, reason: 'a running tunnel is never stopped');
        expect(native.up, isTrue);
        expect(native.intentEnds, 0);
        expect(api.deletes, 0);
        expect(api.creates, 0);
        // Native answers again: the scheduled retry reconciles and connects.
        native.failSnapshots = 0;
        await Future<void>.delayed(const Duration(milliseconds: 200));
        expect(api.creates, greaterThanOrEqualTo(1));
        expect(c.error, isNull);
        await c.disconnect();
      },
    );

    test('a refresh with an unreadable native state stops nothing', () async {
      native
        ..up = true
        ..failSnapshots = 1000;
      final c = controller()..selectedNode = node;
      final phase = c.phase;
      await c.refresh();
      expect(c.error, 'native_state_unknown');
      expect(c.phase, phase);
      expect(native.stops, 0);
      expect(api.calls.where((call) => call.startsWith('DELETE')), isEmpty);
    });
  });

  group('2.3 the user intent survives a missing node list', () {
    test(
      'a node list that failed at startup keeps the saved node and connects after a refresh',
      () async {
        final nodes = NodeListApi(api)..failNodes = true;
        native.desired = true;
        final c = controller(using: nodes);
        await c.refreshNodes();
        expect(c.nodesError, 'network_unavailable');
        c.preferNode(node);
        expect(c.selectedNode, isNull, reason: 'not in the (empty) list yet');
        await c.connect();
        expect(c.error, 'window_node_required');
        expect(
          native.intentEnds,
          0,
          reason: 'the wish to be connected is kept',
        );
        expect(api.calls.where(_apiWork), isEmpty);
        nodes.failNodes = false;
        await Future<void>.delayed(const Duration(milliseconds: 200));
        expect(c.selectedNode, node);
        expect(c.hasLease, isTrue);
        expect(c.error, isNull);
        expect(native.intentEnds, 0);
        await c.disconnect();
      },
    );

    test(
      'no node to wait for: a clear error, no retries, intent kept',
      () async {
        final nodes = NodeListApi(api);
        final c = controller(using: nodes);
        await c.refreshNodes();
        expect(c.nodes, isNotEmpty);
        await c.connect();
        expect(c.error, 'window_node_required');
        final lists = nodes.nodeLists;
        await Future<void>.delayed(const Duration(milliseconds: 100));
        expect(nodes.nodeLists, lists, reason: 'nothing to wait for');
        expect(native.intentEnds, 0);
        expect(messageFor('window_node_required'), contains('Выберите сервер'));
      },
    );
  });

  group('2.4 handshake_not_established', () {
    test(
      'no handshake in time: one retry on a fresh lease, then a clear stop',
      () async {
        native.age = -1;
        api.freshCreates = true;
        final c = controller(handshakeTimeout: const Duration(milliseconds: 30))
          ..selectedNode = node;
        await c.connect();
        expect(c.error, isNull);
        await Future<void>.delayed(const Duration(milliseconds: 50));
        await c.refresh();
        expect(c.error, 'handshake_not_established');
        expect(c.phase, ConnectionPhase.failed);
        expect(
          api.deletes,
          1,
          reason: 'the lease without a handshake is revoked',
        );
        expect(native.intentEnds, 0, reason: 'one retry follows');
        await Future<void>.delayed(const Duration(milliseconds: 100));
        expect(api.creates, 2, reason: 'retried on a fresh lease');
        expect(c.hasLease, isTrue);
        await Future<void>.delayed(const Duration(milliseconds: 50));
        await c.refresh();
        expect(c.error, 'handshake_not_established');
        expect(native.intentEnds, 1, reason: 'terminal after the retry');
        await Future<void>.delayed(const Duration(milliseconds: 100));
        expect(api.creates, 2, reason: 'no third attempt');
        expect(messageFor('handshake_not_established'), contains('20 секунд'));
        expect(
          diagnosticHintKey('handshake_not_established', 'tunnel_monitor'),
          'server_busy',
        );
      },
    );

    test('a handshake in time clears the rule for this window', () async {
      native.age = -1;
      final c = controller(handshakeTimeout: const Duration(milliseconds: 30))
        ..selectedNode = node;
      await c.connect();
      native.age = 3;
      await c.refresh();
      expect(c.error, isNull);
      native.age = -1; // later evidence gaps are not a first-handshake failure
      await Future<void>.delayed(const Duration(milliseconds: 50));
      await c.refresh();
      expect(c.error, isNot('handshake_not_established'));
    });

    test(
      'before the timeout, a missing handshake is only waited for',
      () async {
        native.age = -1;
        final c = controller()..selectedNode = node;
        await c.connect();
        await c.refresh();
        expect(c.error, isNull);
        expect(c.hasLease, isTrue);
      },
    );
  });

  group('2.5 a pending revoke', () {
    test('polls retry the revoke without stopping native each time', () async {
      final c = controller()..selectedNode = node;
      await c.connect();
      api.failDeletes = true;
      await c.disconnect(user: false);
      expect(c.remotePending, isTrue);
      final stops = native.stops;
      final deletes = api.deletes;
      for (var i = 0; i < 3; i++) {
        await c.refresh();
      }
      expect(
        native.stops,
        stops,
        reason: 'native.disconnect() is not repeated',
      );
      expect(api.deletes, deletes + 3);
      api.failDeletes = false;
      await c.retryRevoke();
      expect(c.remotePending, isFalse);
      expect(c.hasLease, isFalse);
    });

    test(
      'force reset frees the UI; the next connect revokes the old row first',
      () async {
        final c = controller()..selectedNode = node;
        await c.connect();
        api.failDeletes = true;
        await c.disconnect(user: false);
        expect(c.remotePending, isTrue);
        await c.forceReset();
        expect(c.remotePending, isFalse);
        expect(c.hasLease, isFalse);
        expect(c.error, isNull);
        expect(c.phase, ConnectionPhase.disconnected);
        expect(native.intentEnds, 1);
        expect(native.up, isFalse);
        // The server still holds the old connection and still refuses to close it: no new one.
        api.oldConnection = true;
        await c.connect();
        expect(c.error, 'revoke_unconfirmed');
        expect(api.creates, 1);
        // Once the server closes it, the next connect goes through.
        await c.forceReset();
        api
          ..failDeletes = false
          ..freshCreates = true;
        final deletes = api.deletes;
        await c.connect();
        expect(api.deletes, deletes + 1);
        expect(api.creates, 2);
        expect(c.hasLease, isTrue);
        await c.disconnect();
      },
    );

    testWidgets('the screen offers «Повторить» and «Сбросить»', (tester) async {
      final c = controller()..selectedNode = node;
      await tester.runAsync(() async {
        await c.connect();
        api.failDeletes = true;
        await c.disconnect(user: false);
      });
      expect(c.remotePending, isTrue);
      await tester.pumpWidget(
        MaterialApp(
          theme: orbitTheme(Brightness.dark),
          home: Scaffold(
            body: ConnectionScreen(controller: c, onSignOut: () async {}),
          ),
        ),
      );
      await tester.pump(const Duration(milliseconds: 300));
      final retry = find.widgetWithText(FilledButton, 'Повторить');
      expect(retry, findsOneWidget);
      expect(tester.widget<FilledButton>(retry).onPressed, isNotNull);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('force-reset')),
        200,
      );
      expect(find.byKey(const ValueKey('force-reset')), findsOneWidget);
      // Leave nothing pending for the next test.
      await tester.runAsync(() => c.forceReset());
    });
  });
}

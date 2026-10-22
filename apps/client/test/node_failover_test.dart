import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/main.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

const a = '00000000-0000-4000-8000-000000000001';
const b = '00000000-0000-4000-8000-000000000002';

// TEST ONLY node/native data. No tunnel traffic or reachability is demonstrated.
class FailoverApi extends FakeApi {
  bool staleA = false;
  bool catalogFails = false;
  bool recoveryReady = false;
  int recoveries = 0;
  final List<String?> chosen = [];
  Completer<void>? catalogGate;

  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (path == '/api/v1/nodes') {
      if (catalogGate != null) await catalogGate!.future;
      if (catalogFails) throw const ClientError('network_unavailable');
      return {
        'nodes': [
          {
            'id': a,
            'name': 'TEST ONLY A',
            'region': 'test-a',
            'reason': staleA ? 'heartbeat_stale' : 'available',
          },
          {
            'id': b,
            'name': 'TEST ONLY B',
            'region': 'test-b',
            'reason': 'available',
          },
        ],
        'checked_at': DateTime.now().toUtc().toIso8601String(),
      };
    }
    if (path.endsWith('/recover')) {
      recoveries++;
      if (!recoveryReady) throw const ClientError('tunnel_recovery_not_ready');
      deletePending = false;
      return {'state': 'revoked'};
    }
    if (method == 'POST' && path == '/api/v1/tunnels')
      chosen.add(body?['node_id'] as String?);
    return super.call(method, path, body);
  }
}

void main() {
  late FailoverApi api;
  late FakeNative native;
  late ConnectionController c;
  setUp(() {
    api = FailoverApi();
    native = FakeNative();
    c = ConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
    );
  });
  tearDown(() => c.dispose());

  test(
    'selected node is pinned in POST and cannot change with an active lease',
    () async {
      await c.refreshNodes();
      c.selectNode(a);
      await c.connect();
      expect(api.chosen, [a]);
      c.selectNode(b);
      expect(c.selectedNode, a);
      api.staleA = true;
      await c.refreshNodes();
      expect(native.up, true);
      expect(api.deletes, 0);
      await c.disconnect();
      c.selectNode(b);
      await c.connect();
      expect(api.chosen, [a, b]);
    },
  );

  test(
    'lost A must finish before B; explicit expiry recovery stops native first',
    () async {
      await c.refreshNodes();
      c.selectNode(a);
      await c.connect();
      api.deletePending = true;
      await c.disconnect();
      expect(c.remotePending, true);
      c.selectNode(b);
      expect(c.selectedNode, a);
      await c.recoverExpired();
      expect(c.error, 'tunnel_recovery_not_ready');
      expect(c.hasLease, true);
      expect(native.up, false);
      expect(api.chosen, [a]);
      api.recoveryReady = true;
      await c.recoverExpired();
      expect(c.hasLease, false);
      expect(c.remotePending, false);
      c.selectNode(b);
      await c.connect();
      expect(api.chosen, [a, b]);
    },
  );

  test('failed native stop never calls recovery or releases intent', () async {
    await c.connect();
    api.deletePending = true;
    await c.disconnect();
    native.failStop = true;
    await c.recoverExpired();
    expect(api.recoveries, 0);
    expect(c.hasLease, true);
  });

  test('catalog errors preserve timestamp and never end a live VPN', () async {
    await c.refreshNodes();
    final checked = c.nodesUpdatedAt;
    await c.connect();
    api.catalogFails = true;
    await c.refreshNodes();
    expect(c.nodesError, 'network_unavailable');
    expect(c.nodesUpdatedAt, checked);
    expect(c.nodes.length, 2);
    expect(native.up, true);
    expect(api.deletes, 0);
  });

  test('disposed controller ignores pending catalog', () async {
    final local = ConnectionController(api, native);
    api.catalogGate = Completer<void>();
    final pending = local.refreshNodes();
    local.dispose();
    api.catalogGate!.complete();
    await pending;
    expect(local.nodes, isEmpty);
  });

  testWidgets('node choices and recovery warning fit narrow large-text UI', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(320, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await c.refreshNodes();
    await c.connect();
    api.deletePending = true;
    await c.disconnect();
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(textScaler: TextScaler.linear(2)),
          child: Scaffold(
            body: ConnectionScreen(controller: c, onSignOut: () async {}),
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.text('TEST ONLY A · test-a'), findsOneWidget);
    expect(find.text('TEST ONLY B · test-b'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Завершить истёкший lease со списанием резерва'),
      300,
    );
    expect(tester.takeException(), isNull);
    c.pausePolling();
  });
}

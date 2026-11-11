import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/latency_probe.dart';
import 'package:vpn_client/region_picker.dart';
import 'package:vpn_client/window_connection.dart';

import 'window_connection_test.dart' show Native;

// TEST ONLY loopback echo and fake catalog. No node, VPN or real network latency is shown.
const fast = '11111111-1111-4111-8111-111111111111';
const silent = '22222222-2222-4222-8222-222222222222';
const legacy = '33333333-3333-4333-8333-333333333333';

class CatalogApi implements TunnelApi {
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (path == '${WindowConnectionController.root}/nodes?include=panel') {
      return {
        'nodes': [
          {
            'id': fast,
            'name': 'TEST ONLY fast',
            'region': 'test-a',
            'available': true,
            'probe_host': '192.0.2.10',
            'probe_port': 51899,
          },
          {
            'id': silent,
            'name': 'TEST ONLY silent',
            'region': 'test-b',
            'available': true,
            'probe_host': '192.0.2.20',
            'probe_port': 51899,
          },
          // A node without the ADR-102 echo shows no ping at all.
          {
            'id': legacy,
            'name': 'TEST ONLY legacy',
            'region': 'test-c',
            'available': true,
          },
        ],
        'checked_at': DateTime.now().toUtc().toIso8601String(),
      };
    }
    throw const ClientError('api_unavailable');
  }
}

class FakeMeter implements LatencyMeter {
  final calls = <String>[];
  @override
  Future<int?> measure(String host, int port) async {
    calls.add('$host:$port');
    return host == '192.0.2.10' ? 42 : null;
  }
}

Future<RawDatagramSocket> echo({bool tamper = false}) async {
  final server = await RawDatagramSocket.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((event) {
    if (event != RawSocketEvent.read) return;
    final d = server.receive();
    if (d == null || d.data.length != 16) return;
    final reply = [0x44, 0x56, 0x52, 0x31, ...d.data.sublist(4)];
    if (tamper) reply[15] ^= 1;
    server.send(reply, d.address, d.port);
  });
  return server;
}

void main() {
  test(
    'UDP meter measures a real loopback echo and checks the nonce',
    () async {
      final good = await echo();
      final bad = await echo(tamper: true);
      addTearDown(good.close);
      addTearDown(bad.close);
      const meter = UdpLatencyMeter(timeout: Duration(milliseconds: 300));
      final ms = await meter.measure('127.0.0.1', good.port);
      expect(ms, isNotNull);
      expect(ms, greaterThanOrEqualTo(1));
      expect(await meter.measure('127.0.0.1', bad.port), isNull);
      // Only IPv4 literals and unprivileged ports are ever probed.
      expect(await meter.measure('example.invalid', 51899), isNull);
      expect(await meter.measure('::1', 51899), isNull);
      expect(await meter.measure('127.0.0.1', 53), isNull);
    },
  );

  test('labels and bands', () {
    expect(latencyLabel(42), '42 мс');
    expect(latencyLabel(null), 'нет ответа');
    expect(latencyBand(79), LatencyBand.good);
    expect(latencyBand(80), LatencyBand.fair);
    expect(latencyBand(200), LatencyBand.poor);
    expect(latencyBand(null), LatencyBand.poor);
  });

  test('catalog refresh measures only nodes that advertise an echo', () async {
    final meter = FakeMeter();
    final c = WindowConnectionController(
      CatalogApi(),
      Native(),
      pollInterval: const Duration(days: 1),
    )..latencyMeter = meter;
    addTearDown(c.dispose);
    await c.refreshNodes();
    await pumpEventQueue();
    expect(meter.calls..sort(), ['192.0.2.10:51899', '192.0.2.20:51899']);
    expect(c.latencyMs, {fast: 42, silent: null});
    expect(c.latencyMeasuring, isFalse);
  });

  testWidgets('server list shows measured ping and no answer', (tester) async {
    final c = WindowConnectionController(
      CatalogApi(),
      Native(),
      pollInterval: const Duration(days: 1),
    )..latencyMeter = FakeMeter();
    addTearDown(c.dispose);
    await tester.runAsync(() async {
      await c.refreshNodes();
      await pumpEventQueue();
    });
    await tester.pumpWidget(
      MaterialApp(home: RegionPickerScreen(controller: c)),
    );
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('42 мс'), findsOneWidget);
    expect(find.text('нет ответа'), findsOneWidget);
    expect(find.text('Обновить список и пинг'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

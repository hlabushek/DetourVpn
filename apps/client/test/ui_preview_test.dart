import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/quota.dart';
import 'package:vpn_client/quota_screen.dart';
import 'connection_test.dart' show FakeNative;
import 'node_failover_test.dart' show FailoverApi;
import 'quota_test.dart' show QuotaApi, entry;

// TEST ONLY: render real widgets without credentials, API calls or a live VPN.
void main() {
  setUpAll(() async {
    final root =
        Platform.environment['FLUTTER_ROOT'] ??
        '${Platform.environment['LOCALAPPDATA']}/ProjectVpn/toolchains/flutter';
    for (final font in {
      'Roboto': 'roboto-regular.ttf',
      'MaterialIcons': 'materialicons-regular.otf',
    }.entries) {
      final bytes = File(
        '$root/bin/cache/artifacts/material_fonts/${font.value}',
      ).readAsBytesSync();
      await (FontLoader(
        font.key,
      )..addFont(Future.value(ByteData.sublistView(bytes)))).load();
    }
  });
  testWidgets('render credential-free login and disconnected UI previews', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(430, 1040);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final base =
        Platform.environment['LOCALAPPDATA'] ?? Directory.systemTemp.path;
    final directory = Directory('$base/ProjectVpn/i013-preview')
      ..createSync(recursive: true);
    Future<void> capture(Widget widget, String name) async {
      final key = GlobalKey();
      await tester.pumpWidget(RepaintBoundary(key: key, child: widget));
      await tester.pumpAndSettle();
      final boundary =
          key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
      await tester.runAsync(() async {
        final rendered = await boundary.toImage(pixelRatio: 2);
        final data = (await rendered.toByteData(
          format: ui.ImageByteFormat.png,
        ))!;
        File(
          '${directory.path}/$name.png',
        ).writeAsBytesSync(data.buffer.asUint8List());
        rendered.dispose();
      });
      expect(tester.takeException(), isNull);
    }

    final api = Api(Uri.parse('https://example.invalid'));
    await capture(VpnApp(api: api, native: FakeNative()), 'login');
    final controller = ConnectionController(FailoverApi(), FakeNative());
    await controller.refreshNodes();
    addTearDown(controller.dispose);
    final quotaApi = QuotaApi()
      ..totals = {
        'granted_bytes': 16777216,
        'used_bytes': 1048576,
        'reserved_bytes': 1048576,
        'available_bytes': 14680064,
        'revision': 2,
      }
      ..page = {
        'items': [
          {
            ...entry(2),
            'kind': 'debit',
            'delta_bytes': -1048576,
            'balance_after_bytes': 15728640,
            'source_type': 'tunnel_lease',
          },
          {
            ...entry(1),
            'delta_bytes': 16777216,
            'balance_after_bytes': 16777216,
            'source_type': 'registration',
          },
        ],
        'snapshot_revision': 2,
        'next_before_sequence': null,
      };
    final quota = QuotaController(quotaApi);
    addTearDown(quota.dispose);
    await quota.refresh();
    for (final brightness in Brightness.values) {
      await capture(
        MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
            brightness: brightness,
            colorSchemeSeed: const Color(0xff206c60),
            useMaterial3: true,
          ),
          home: QuotaScreen(controller: quota),
        ),
        'quota-${brightness.name}',
      );
      await capture(
        MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
            colorSchemeSeed: const Color(0xff206c60),
            brightness: brightness,
            useMaterial3: true,
          ),
          home: Scaffold(
            appBar: AppBar(title: const Text('VPN')),
            body: ConnectionScreen(
              controller: controller,
              onSignOut: () async {},
            ),
          ),
        ),
        'disconnected-${brightness.name}',
      );
    }
  });
}

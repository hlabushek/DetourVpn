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
import 'package:vpn_client/orbit_theme.dart';
import 'connection_test.dart' show FakeNative;
import 'node_failover_test.dart' show FailoverApi;
import 'quota_test.dart' show QuotaApi, entry;

// TEST ONLY presentation data, never included in main.dart or release assets.
class PreviewApi extends Api {
  PreviewApi(this.quota) : super(Uri.parse('https://example.invalid'));
  final QuotaApi quota;
  final nodes = FailoverApi();
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) {
    if (path.startsWith('/api/v1/quota')) return quota.call(method, path, body);
    return nodes.call(method, path, body);
  }
}

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
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final base =
        Platform.environment['LOCALAPPDATA'] ?? Directory.systemTemp.path;
    final directory = Directory('$base/ProjectVpn/i013-preview')
      ..createSync(recursive: true);
    Future<void> capture(
      Widget widget,
      String name, {
      Future<void> Function()? prepare,
    }) async {
      final key = GlobalKey();
      await tester.pumpWidget(RepaintBoundary(key: key, child: widget));
      await tester.pumpAndSettle();
      if (prepare != null) {
        await prepare();
        await tester.pumpAndSettle();
      }
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
          theme: orbitTheme(brightness),
          home: QuotaScreen(controller: quota),
        ),
        'quota-${brightness.name}',
      );
      await capture(
        MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: orbitTheme(brightness),
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
      for (final destination in ['VPN', 'Трафик', 'Профиль']) {
        final previewNative = FakeNative()..age = 0;
        await capture(
          VpnApp(
            key: UniqueKey(),
            api: PreviewApi(quotaApi),
            native: previewNative,
          ),
          'shell-${brightness.name}-$destination',
          prepare: () async {
            if (brightness == Brightness.light) {
              await tester.tap(find.byTooltip('Сменить тему'));
              await tester.pumpAndSettle();
            }
            tester.widget<LoginScreen>(find.byType(LoginScreen)).onSignedIn();
            await tester.pumpAndSettle();
            if (destination == 'VPN') {
              await tester.tap(find.text('Подключить'));
              await tester.pumpAndSettle();
              final c = tester
                  .widget<ConnectionScreen>(find.byType(ConnectionScreen))
                  .controller;
              await c.refresh();
            } else {
              await tester.tap(find.text(destination));
            }
          },
        );
      }
    }
    await tester.pumpWidget(const SizedBox());
  });
}

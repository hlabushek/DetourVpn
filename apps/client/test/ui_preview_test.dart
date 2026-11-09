import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/orbit_widgets.dart';
import 'package:vpn_client/profile_screen.dart';
import 'connection_test.dart' show FakeNative;
import 'node_failover_test.dart' show FailoverApi;
import 'quota_test.dart' show QuotaApi, entry;

// TEST ONLY mock catalog for screenshots: these servers, cities and amounts are
// demo data, never shipped and never evidence of a reachable node.
const _demoNodes = [
  ('FI', 'Хельсинки', 'available'),
  ('FI', 'Тампере', 'available'),
  ('FI', 'Оулу', 'available'),
  ('DE', 'Франкфурт', 'available'),
  ('DE', 'Берлин', 'available'),
  ('DE', 'Гамбург', 'capacity'),
  ('NL', 'Амстердам', 'capacity'),
  ('NL', 'Роттердам', 'heartbeat_stale'),
];

class _CatalogApi extends FailoverApi {
  _CatalogApi(this.quota);
  final QuotaApi quota;
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (path.startsWith('/api/v1/quota')) return quota.call(method, path, body);
    if (path == '/api/v1/nodes') {
      return {
        'nodes': [
          for (final (i, n) in _demoNodes.indexed)
            {
              'id':
                  '00000000-0000-4000-8000-${(200 + i).toString().padLeft(12, '0')}',
              'name': 'TEST ONLY ${i + 1}',
              'region': 'test-$i',
              'country_code': n.$1,
              'city': n.$2,
              'reason': n.$3,
            },
        ],
        'checked_at': DateTime.now().toUtc().toIso8601String(),
      };
    }
    return super.call(method, path, body);
  }
}

/// TEST ONLY Api for the shell; delegates everything to the mock catalog.
class PreviewApi extends Api {
  PreviewApi(QuotaApi quota)
    : nodes = _CatalogApi(quota),
      super(Uri.parse('https://example.invalid'));
  final _CatalogApi nodes;
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) => nodes.call(method, path, body);
}

QuotaApi _demoQuota() => QuotaApi()
  ..totals = {
    'granted_bytes': 50000000000,
    'used_bytes': 37600000000,
    'reserved_bytes': 0,
    'available_bytes': 12400000000,
    'revision': 3,
  }
  ..page = {
    'items': [
      {
        ...entry(3),
        'delta_bytes': 10000000000,
        'balance_after_bytes': 12400000000,
        'source_type': 'internal',
      },
      {
        ...entry(2),
        'kind': 'debit',
        'delta_bytes': -2300000000,
        'balance_after_bytes': 2400000000,
        'source_type': 'peer_window',
      },
      {
        ...entry(1),
        'delta_bytes': 1000000000,
        'balance_after_bytes': 4700000000,
        'source_type': 'reward',
      },
    ],
    'snapshot_revision': 3,
    'next_before_sequence': null,
  };

// TEST ONLY: render real widgets without credentials, API calls or a live VPN.
// PNGs go to %LOCALAPPDATA%/ProjectVpn/soft-orbit-preview for owner review.
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

  testWidgets('render Soft Orbit previews on TEST ONLY data', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final base =
        Platform.environment['LOCALAPPDATA'] ?? Directory.systemTemp.path;
    final directory = Directory('$base/ProjectVpn/soft-orbit-preview')
      ..createSync(recursive: true);
    final rootKey = GlobalKey();

    Future<void> snap(String name) async {
      await tester.pumpAndSettle();
      // Raster artwork decodes asynchronously; finish it before capturing.
      final images = tester.widgetList<Image>(find.byType(Image)).toList();
      await tester.runAsync(() async {
        for (final image in images) {
          await precacheImage(
            image.image,
            tester.element(find.byWidget(image).first),
          );
        }
      });
      await tester.pumpAndSettle();
      final boundary =
          rootKey.currentContext!.findRenderObject()! as RenderRepaintBoundary;
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

    Future<void> host(Widget child) async {
      await tester.pumpWidget(RepaintBoundary(key: rootKey, child: child));
      await tester.pumpAndSettle();
    }

    for (final brightness in Brightness.values) {
      final b = brightness.name;
      final light = brightness == Brightness.light;

      Future<void> toLight() async {
        if (!light) return;
        await tester.tap(find.byTooltip('Настройки'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Оформление'));
        await tester.pumpAndSettle();
        await tester.pageBack();
        await tester.pumpAndSettle();
      }

      // Login: welcome and device code.
      await host(
        VpnApp(
          key: UniqueKey(),
          api: Api(Uri.parse('https://example.invalid')),
          native: FakeNative(),
        ),
      );
      await toLight();
      await snap('$b-01-welcome');
      await tester.tap(find.byKey(const Key('consent-check')));
      await tester.pump();
      await tester.tap(find.byKey(const Key('link-code-open')));
      await snap('$b-02-device-code');

      // Signed-in shell on the TEST ONLY catalog.
      final api = PreviewApi(_demoQuota());
      await host(
        VpnApp(key: UniqueKey(), api: api, native: FakeNative()..age = 0),
      );
      await toLight();
      tester.widget<LoginScreen>(find.byType(LoginScreen)).onSignedIn();
      await tester.pumpAndSettle();
      await tester.tap(find.text('Сменить регион'));
      await snap('$b-04-regions');
      await tester.tap(find.text('Хельсинки'));
      await tester.pumpAndSettle();
      await snap('$b-03-home-disconnected');
      await tester.tap(find.text('Подключить'));
      await tester.pumpAndSettle();
      await tester
          .widget<ConnectionScreen>(find.byType(ConnectionScreen))
          .controller
          .refresh();
      await snap('$b-05-home-connected');
      await tester.tap(find.text('Трафик'));
      await snap('$b-06-traffic');
      await tester.tap(find.text('Профиль').last);
      await snap('$b-07-profile-owner');
      await tester.tap(find.byTooltip('Настройки'));
      await snap('$b-08-settings');
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester.tap(find.text('VPN'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Отключить VPN'));
      await tester.pumpAndSettle();

      // Error state: the TEST ONLY API refuses the next tunnel.
      api.nodes.rejectCreate = 'quota_exhausted';
      await tester.tap(find.text('Подключить'));
      await snap('$b-09-home-error');

      // Guest profile and empty catalog, rendered standalone.
      final guest = ConnectionController(
        PreviewApi(_demoQuota()),
        FakeNative(),
      );
      addTearDown(guest.dispose);
      await host(
        MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: orbitTheme(brightness),
          home: Scaffold(
            body: OrbitBackdrop(
              child: SafeArea(
                child: ProfileScreen(
                  controller: guest,
                  guest: true,
                  onSignOut: () async {},
                  onDeleteAccount: () async {},
                  onLeaveDevice: () async {},
                ),
              ),
            ),
          ),
        ),
      );
      await snap('$b-10-profile-guest');
    }
    await tester.pumpWidget(const SizedBox());
  });
}

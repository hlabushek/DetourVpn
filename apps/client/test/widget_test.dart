import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/connection_screen.dart' show ProfileScreen;
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/quota_screen.dart';
import 'package:vpn_client/orbit_widgets.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

// TEST ONLY API; no real login, credentials or network requests.
class ShellApi extends Api {
  ShellApi() : super(Uri.parse('https://example.invalid'));
  final fake = FakeApi();
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) => fake.call(method, path, body);
}

void main() {
  test(
    'compact units preserve integer boundaries without double conversion',
    () {
      expect(compactBytes(0), '0 Б');
      expect(compactBytes(999), '999 Б');
      expect(compactBytes(1000), '1,0 КБ');
      expect(compactBytes(1048576), '1,0 МБ');
      expect(compactBytes(9223372036854775807), '9,2 ЭБ');
    },
  );
  testWidgets('tabs preserve connection and theme; logout returns to login', (
    tester,
  ) async {
    final native = FakeNative()..age = 0;
    await tester.pumpWidget(VpnApp(api: ShellApi(), native: native));
    tester.widget<LoginScreen>(find.byType(LoginScreen)).onSignedIn();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Подключить'));
    await tester.pumpAndSettle();
    expect(native.up, true);
    await tester.tap(find.text('Трафик'));
    await tester.pumpAndSettle();
    expect(find.byType(QuotaScreen), findsOneWidget);
    expect(native.up, true);
    await tester.tap(find.text('Профиль').last);
    await tester.pumpAndSettle();
    expect(find.byType(ProfileScreen), findsOneWidget);
    await tester.tap(find.text('Оформление'));
    await tester.pumpAndSettle();
    expect(find.text('Светлая тема'), findsOneWidget);
    expect(native.up, true);
    await tester.scrollUntilVisible(find.text('Выйти из аккаунта'), 200);
    await tester.tap(find.text('Выйти из аккаунта'));
    await tester.pumpAndSettle();
    expect(native.up, false);
    expect(find.byType(LoginScreen), findsOneWidget);
    expect(find.byType(NavigationBar), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
    'large text, narrow screen and dark/light themes retain controls',
    (tester) async {
      tester.view.physicalSize = const Size(320, 640);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final c = ConnectionController(FakeApi(), FakeNative());
      addTearDown(c.dispose);
      for (final brightness in Brightness.values) {
        await tester.pumpWidget(
          MaterialApp(
            key: ValueKey(brightness),
            theme: orbitTheme(brightness),
            home: MediaQuery(
              data: const MediaQueryData(textScaler: TextScaler.linear(2)),
              child: Scaffold(
                body: ConnectionScreen(controller: c, onSignOut: () async {}),
              ),
            ),
          ),
        );
        expect(find.text('Отключено'), findsOneWidget);
        await tester.scrollUntilVisible(find.text('Подключить'), 200);
        expect(tester.takeException(), isNull);
      }
    },
  );
  testWidgets('missing API produces explicit setup screen', (tester) async {
    await tester.pumpWidget(VpnApp(api: null, native: FakeNative()));
    expect(find.textContaining('API не настроен'), findsOneWidget);
    expect(find.text('Подключено'), findsNothing);
  });
  testWidgets(
    'connected action disconnects; profile remains usable at large text',
    (tester) async {
      final native = FakeNative()..age = 0;
      final c = ConnectionController(FakeApi(), native);
      addTearDown(c.dispose);
      await c.connect();
      await c.refresh();
      await tester.pumpWidget(
        MaterialApp(
          theme: orbitTheme(Brightness.dark),
          home: Scaffold(
            body: ConnectionScreen(controller: c, onSignOut: () async {}),
          ),
        ),
      );
      expect(find.text('Подключено'), findsOneWidget);
      expect(find.text('Подключить'), findsNothing);
      await tester.tap(find.text('Отключить VPN'));
      await tester.pumpAndSettle();
      expect(native.up, false);
      expect(find.text('Подключить'), findsOneWidget);
      c.pausePolling();
      tester.view.physicalSize = const Size(320, 640);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var signedOut = false;
      await tester.pumpWidget(
        MaterialApp(
          theme: orbitTheme(Brightness.light),
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: Scaffold(
              body: ProfileScreen(
                controller: c,
                onTheme: () {},
                onSignOut: () async {
                  signedOut = true;
                },
              ),
            ),
          ),
        ),
      );
      await tester.scrollUntilVisible(find.text('Выйти из аккаунта'), 250);
      await tester.tap(find.text('Выйти из аккаунта'));
      expect(signedOut, true);
      expect(tester.takeException(), isNull);
    },
  );
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/ui/glass_nav.dart';
import 'package:vpn_client/connection_screen.dart'
    show ProfileScreen, SettingsScreen;
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/session_store.dart';
import 'package:vpn_client/quota_screen.dart';
import 'package:vpn_client/orbit_widgets.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

// TEST ONLY API; no real login, credentials or network requests.
class ShellApi extends Api {
  ShellApi({SessionStore? store})
    : super(Uri.parse('https://example.invalid'), store: store);
  final fake = FakeApi();
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) {
    if (method == 'POST' && path == '/api/v1/auth/start') {
      return Future.value({
        'request_id': '11111111-1111-4111-8111-111111111111',
        'verifier': 'TEST ONLY VERIFIER',
        'bot_url': 'https://t.me/example_bot?start=TEST_ONLY',
        'expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(minutes: 10))
            .toIso8601String(),
      });
    }
    return fake.call(method, path, body);
  }
}

class _WidgetSessionStore implements SessionStore, PendingLoginStore {
  Map<String, dynamic>? session;
  Map<String, dynamic>? pendingLogin;
  bool failRead = false;
  int reads = 0;
  int writes = 0;

  @override
  Future<Map<String, dynamic>?> read() async {
    reads++;
    if (failRead) throw StateError('TEST ONLY keystore read error');
    return session;
  }

  @override
  Future<void> write(Map<String, dynamic>? value) async {
    writes++;
    session = value;
  }

  @override
  Future<Map<String, dynamic>?> readPendingLogin() async => pendingLogin;

  @override
  Future<void> writePendingLogin(Map<String, dynamic>? value) async =>
      pendingLogin = value;
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
    await tester.tap(find.byTooltip('Настройки'));
    await tester.pumpAndSettle();
    expect(find.byType(SettingsScreen), findsOneWidget);
    await tester.tap(find.text('Оформление'));
    await tester.pumpAndSettle();
    expect(find.text('Светлая тема'), findsOneWidget);
    expect(native.up, true);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byType(ProfileScreen), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Выйти из аккаунта'), 200);
    await tester.tap(find.text('Выйти из аккаунта'));
    await tester.pumpAndSettle();
    expect(native.up, false);
    expect(find.byType(LoginScreen), findsOneWidget);
    expect(find.byType(GlassNavBar), findsNothing);
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
  testWidgets(
    'ADR-158: a cold-start session read failure never erases the session',
    (tester) async {
      final store = _WidgetSessionStore()
        ..session = {
          'origin': 'https://example.invalid',
          'refresh_token': 'TEST ONLY',
        }
        ..failRead = true;
      await tester.pumpWidget(
        VpnApp(
          api: ShellApi(store: store),
          native: FakeNative(),
        ),
      );
      // The bounded retry schedule (1+2+4+8+15 s) runs out with every read failing.
      for (var i = 0; i < 40; i++) {
        await tester.pump(const Duration(seconds: 1));
      }
      expect(store.reads, 6);
      expect(store.writes, 0);
      expect(store.session!['refresh_token'], 'TEST ONLY');
      expect(find.byType(LoginScreen), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    },
  );
  testWidgets('missing API produces explicit setup screen', (tester) async {
    await tester.pumpWidget(VpnApp(api: null, native: FakeNative()));
    expect(find.textContaining('API не настроен'), findsOneWidget);
    expect(find.text('Подключено'), findsNothing);
  });
  testWidgets(
    'pending login survives app recreation and expired challenge clears',
    (tester) async {
      final store = _WidgetSessionStore();
      var api = ShellApi(store: store);
      await tester.pumpWidget(VpnApp(api: api, native: FakeNative()));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('consent-check')));
      await tester.tap(find.byKey(const Key('consent-check')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Войти через Telegram'));
      await tester.tap(find.text('Войти через Telegram'));
      await tester.pumpAndSettle();
      expect(store.pendingLogin, isNotNull);
      expect(find.text('Открыть бота в Telegram'), findsOneWidget);
      expect(
        find.byWidgetPredicate(
          (widget) =>
              widget is TextField &&
              widget.decoration?.labelText == 'Код из Telegram',
        ),
        findsOneWidget,
      );

      await tester.pumpWidget(const SizedBox());
      api = ShellApi(store: store);
      await tester.pumpWidget(VpnApp(api: api, native: FakeNative()));
      await tester.pumpAndSettle();
      expect(find.text('Получить новый код входа'), findsOneWidget);
      expect(find.text('Открыть бота в Telegram'), findsOneWidget);
      final restoredCodeField = find.byWidgetPredicate(
        (widget) =>
            widget is TextField &&
            widget.decoration?.labelText == 'Код из Telegram',
      );
      await tester.scrollUntilVisible(
        restoredCodeField,
        200,
        scrollable: find.byType(Scrollable).first,
      );
      expect(restoredCodeField, findsOneWidget);

      await tester.pumpWidget(const SizedBox());
      store.pendingLogin!['expires_at'] = DateTime.now()
          .toUtc()
          .subtract(const Duration(seconds: 1))
          .toIso8601String();
      api = ShellApi(store: store);
      await tester.pumpWidget(VpnApp(api: api, native: FakeNative()));
      await tester.pumpAndSettle();
      expect(find.text('Открыть бота в Telegram'), findsNothing);
      expect(store.pendingLogin, isNull);
      expect(
        find.text(
          'Сохранённая попытка входа больше недействительна. Запросите новый код.',
        ),
        findsOneWidget,
      );
    },
  );
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
      await tester.pumpWidget(
        MaterialApp(
          theme: orbitTheme(Brightness.light),
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: SettingsScreen(onTheme: () {}, controller: c),
          ),
        ),
      );
      await tester.scrollUntilVisible(
        find.text('Системные настройки VPN'),
        250,
      );
      expect(find.text('Системные настройки VPN'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.textContaining('ещё не принято'),
        250,
      );
      expect(find.textContaining('ещё не принято'), findsOneWidget);
      var signedOut = false;
      var deleted = 0;
      await tester.pumpWidget(
        MaterialApp(
          theme: orbitTheme(Brightness.light),
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: Scaffold(
              body: ProfileScreen(
                controller: c,
                onSignOut: () async {
                  signedOut = true;
                },
                onDeleteAccount: () async {
                  deleted += 1;
                },
              ),
            ),
          ),
        ),
      );
      await tester.scrollUntilVisible(find.text('Выйти из аккаунта'), 250);
      await tester.tap(find.text('Выйти из аккаунта'));
      expect(signedOut, true);
      // Cancel keeps the account; only the explicit confirmation calls the callback.
      await tester.scrollUntilVisible(
        find.byKey(const Key('delete-account-button')),
        250,
      );
      await tester.tap(find.byKey(const Key('delete-account-button')));
      await tester.pumpAndSettle();
      expect(find.text('Удалить аккаунт?'), findsOneWidget);
      await tester.tap(find.byKey(const Key('delete-account-cancel')));
      await tester.pumpAndSettle();
      expect(deleted, 0);
      await tester.tap(find.byKey(const Key('delete-account-button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('delete-account-confirm')));
      await tester.pumpAndSettle();
      expect(deleted, 1);
      expect(tester.takeException(), isNull);
    },
  );
}

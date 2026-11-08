import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/connection_screen.dart' show ProfileScreen;
import 'package:vpn_client/main.dart';
import 'package:vpn_client/session_store.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

// TEST ONLY: no real login, credentials, keys or network. Redeem responses are synthetic.
class _Store implements SessionStore, PendingLoginStore {
  Map<String, dynamic>? session;
  Map<String, dynamic>? pending;
  @override
  Future<Map<String, dynamic>?> read() async => session;
  @override
  Future<void> write(Map<String, dynamic>? value) async => session = value;
  @override
  Future<Map<String, dynamic>?> readPendingLogin() async => pending;
  @override
  Future<void> writePendingLogin(Map<String, dynamic>? value) async =>
      pending = value;
}

class _RedeemApi extends Api {
  _RedeemApi(this.store)
    : super(Uri.parse('https://example.invalid'), store: store);
  final _Store store;
  final List<Map<String, dynamic>> redeemed = [];
  String? redeemError;
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (method == 'POST' && path == '/api/v1/auth/link/redeem') {
      redeemed.add(body!);
      if (redeemError != null) throw ClientError(redeemError!);
      return {
        'access_token': 'a' * 43,
        'refresh_token': 'r' * 43,
        'access_expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(minutes: 10))
            .toIso8601String(),
        'refresh_expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(days: 30))
            .toIso8601String(),
      };
    }
    throw const ClientError('network_unavailable');
  }
}

void _tall(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Widget _host(Widget child) => MaterialApp(home: Scaffold(body: child));

void main() {
  testWidgets('a device code signs in as a guest without Telegram', (
    tester,
  ) async {
    _tall(tester);
    final api = _RedeemApi(_Store());
    var signedIn = 0;
    await tester.pumpWidget(
      _host(
        LoginScreen(
          api: api,
          native: FakeNative(),
          onSignedIn: () => signedIn++,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('link-code-field')), findsNothing);
    await tester.tap(find.byKey(const Key('link-code-open')));
    await tester.pump();
    await tester.enterText(
      find.byKey(const Key('link-code-field')),
      ' abcd-2345 ',
    );
    await tester.tap(find.byKey(const Key('link-code-submit')));
    await tester.pumpAndSettle();
    expect(api.redeemed, [
      {'code': 'abcd-2345'},
    ]);
    expect(signedIn, 1);
    expect(api.authenticated, isTrue);
    expect(api.store.session?['refresh_token'], 'r' * 43);
  });

  testWidgets('an invalid code shows a plain message and stays signed out', (
    tester,
  ) async {
    _tall(tester);
    final api = _RedeemApi(_Store())..redeemError = 'invalid_link_code';
    var signedIn = 0;
    await tester.pumpWidget(
      _host(
        LoginScreen(
          api: api,
          native: FakeNative(),
          onSignedIn: () => signedIn++,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('link-code-open')));
    await tester.pump();
    await tester.enterText(find.byKey(const Key('link-code-field')), 'ZZZZ');
    await tester.tap(find.byKey(const Key('link-code-submit')));
    await tester.pumpAndSettle();
    expect(find.textContaining('недействителен'), findsOneWidget);
    expect(signedIn, 0);
    expect(api.authenticated, isFalse);
  });

  testWidgets('guest profile hides account actions and can leave the account', (
    tester,
  ) async {
    _tall(tester);
    final controller = ConnectionController(FakeApi(), FakeNative());
    var left = 0;
    var deleted = 0;
    await tester.pumpWidget(
      _host(
        ProfileScreen(
          onSignOut: () async {},
          onDeleteAccount: () async => deleted++,
          onTheme: () {},
          controller: controller,
          guest: true,
          onLeaveDevice: () async => left++,
        ),
      ),
    );
    expect(find.text('Гостевое устройство'), findsOneWidget);
    expect(find.byKey(const Key('delete-account-button')), findsNothing);
    expect(find.text('Выйти из аккаунта'), findsNothing);
    await tester.scrollUntilVisible(
      find.byKey(const Key('leave-device-button')),
      200,
    );
    await tester.tap(find.byKey(const Key('leave-device-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('leave-device-cancel')));
    await tester.pumpAndSettle();
    expect(left, 0);
    await tester.tap(find.byKey(const Key('leave-device-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('leave-device-confirm')));
    await tester.pumpAndSettle();
    expect((left, deleted), (1, 0));
  });

  testWidgets('owner profile keeps sign-out and account deletion', (
    tester,
  ) async {
    _tall(tester);
    final controller = ConnectionController(FakeApi(), FakeNative());
    await tester.pumpWidget(
      _host(
        ProfileScreen(
          onSignOut: () async {},
          onDeleteAccount: () async {},
          onTheme: () {},
          controller: controller,
        ),
      ),
    );
    await tester.scrollUntilVisible(
      find.byKey(const Key('delete-account-button')),
      200,
    );
    expect(find.byKey(const Key('leave-device-button')), findsNothing);
    expect(find.text('Выйти из аккаунта'), findsOneWidget);
  });
}

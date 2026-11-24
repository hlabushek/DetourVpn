import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/home_screen.dart';
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/orbit_widgets.dart';
import 'package:vpn_client/primary_action.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

/// TEST ONLY: a controller whose state is set directly; connect/disconnect are recorded and
/// never reach an API or a tunnel. Proves the home-screen mapping only (ADR-192).
class _StateController extends ConnectionController {
  _StateController() : super(FakeApi(), FakeNative());
  bool fakeBusy = false;
  bool fakeDisconnecting = false;
  bool fakeLease = false;
  final List<String> calls = [];

  @override
  bool get busy => fakeBusy || fakeDisconnecting;
  @override
  bool get disconnecting => fakeDisconnecting;
  @override
  bool get hasLease => fakeLease;
  @override
  Future<void> connect() async => calls.add('connect');
  @override
  Future<void> disconnect() async => calls.add('disconnect');
  @override
  Future<void> refresh() async {}
  @override
  Future<void> refreshQuota() async {}
}

void main() {
  late _StateController c;

  setUp(() {
    c = _StateController();
    PrimaryActionGate.shared.reset();
  });
  tearDown(() => c.dispose());

  Future<void> show(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: orbitTheme(Brightness.dark),
        home: Scaffold(
          body: ConnectionScreen(
            controller: c,
            onSignOut: () async => c.calls.add('signOut'),
            onOpenTraffic: () => c.calls.add('openTraffic'),
          ),
        ),
      ),
    );
    await tester.pump(const Duration(milliseconds: 400));
  }

  Future<void> close(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
  }

  /// The title, the single button label, and what one tap on it does.
  Future<void> expectScreen(
    WidgetTester tester, {
    required String title,
    required String label,
    required List<String> tap,
  }) async {
    await show(tester);
    expect(find.text(title), findsOneWidget);
    expect(find.text(label), findsOneWidget);
    // Only one primary label is on the screen at a time.
    for (final other in {
      'Подключить',
      'Подключить сейчас',
      'Отменить подключение',
      'Отключить VPN',
      'Повторить',
      'Отключение…',
      'Войти снова',
      'Открыть трафик',
    }.difference({label})) {
      expect(find.text(other), findsNothing, reason: other);
    }
    await tester.tap(find.text(label));
    await tester.pump();
    expect(c.calls, tap);
    await close(tester);
  }

  testWidgets('VPN off: «Отключено» / «Подключить» connects', (tester) async {
    await expectScreen(
      tester,
      title: 'Отключено',
      label: 'Подключить',
      tap: ['connect'],
    );
  });

  testWidgets('failed: «Ошибка подключения» / «Подключить» connects', (
    tester,
  ) async {
    c
      ..phase = ConnectionPhase.failed
      ..error = 'handshake_not_established'
      ..detail =
          'Ожидается handshake'; // A stale step text is not shown as status.
    await show(tester);
    expect(find.text('Ожидается handshake'), findsNothing);
    expect(find.text('VPN не подключён. Причина указана ниже'), findsOneWidget);
    await close(tester);
    await expectScreen(
      tester,
      title: 'Ошибка подключения',
      label: 'Подключить',
      tap: ['connect'],
    );
  });

  testWidgets('connecting: «Отменить подключение» stops the attempt', (
    tester,
  ) async {
    c
      ..phase = ConnectionPhase.connecting
      ..fakeBusy = true;
    await expectScreen(
      tester,
      title: 'Подключение',
      label: 'Отменить подключение',
      tap: ['disconnect'],
    );
  });

  testWidgets(
    'a tap waiting for a check shows «Подключение», not «Отключено»',
    (tester) async {
      c.fakeBusy = true; // ADR-154: phase stays disconnected meanwhile.
      await expectScreen(
        tester,
        title: 'Подключение',
        label: 'Отменить подключение',
        tap: ['disconnect'],
      );
    },
  );

  testWidgets('connected: «Подключено» / «Отключить VPN»', (tester) async {
    c
      ..phase = ConnectionPhase.connected
      ..fakeLease = true;
    await expectScreen(
      tester,
      title: 'Подключено',
      label: 'Отключить VPN',
      tap: ['disconnect'],
    );
  });

  testWidgets(
    'recovering without a lease offers «Подключить сейчас», which connects',
    (tester) async {
      c
        ..phase = ConnectionPhase.reconnecting
        ..detail = 'Восстанавливается подключение';
      await expectScreen(
        tester,
        title: 'Восстановление связи',
        label: 'Подключить сейчас',
        tap: ['connect'],
      );
    },
  );

  testWidgets('recovering with a lease offers «Отключить VPN»', (tester) async {
    c
      ..phase = ConnectionPhase.reconnecting
      ..fakeLease = true
      ..detail = 'Сеть сменилась, восстанавливается связь';
    await expectScreen(
      tester,
      title: 'Восстановление связи',
      label: 'Отключить VPN',
      tap: ['disconnect'],
    );
  });

  for (final error in ['unauthorized', 'session_device_mismatch']) {
    testWidgets('$error: «Войти снова» signs out, no connect', (tester) async {
      c
        ..phase = ConnectionPhase.failed
        ..error = error;
      await show(tester);
      expect(find.text('VPN не подключён. Нужно войти снова'), findsOneWidget);
      await close(tester);
      await expectScreen(
        tester,
        title: 'Ошибка подключения',
        label: 'Войти снова',
        tap: ['signOut'],
      );
    });
  }

  testWidgets('insufficient_quota: «Открыть трафик», no connect button', (
    tester,
  ) async {
    c
      ..phase = ConnectionPhase.failed
      ..error = 'insufficient_quota';
    await show(tester);
    expect(find.text('VPN не подключён: недостаточно трафика'), findsOneWidget);
    await close(tester);
    await expectScreen(
      tester,
      title: 'Ошибка подключения',
      label: 'Открыть трафик',
      tap: ['openTraffic'],
    );
  });

  testWidgets('a running disconnect is a disabled «Отключение…»', (
    tester,
  ) async {
    c
      ..phase = ConnectionPhase.connected
      ..fakeLease = true
      ..fakeDisconnecting = true;
    await show(tester);
    expect(find.text('Отключение'), findsOneWidget);
    expect(find.text('Отменить подключение'), findsNothing);
    final button = find.widgetWithText(FilledButton, 'Отключение…');
    expect(tester.widget<FilledButton>(button).onPressed, isNull);
    // The orbit is the same action: nothing either.
    await tester.tap(find.byType(OrbitBlob), warnIfMissed: false);
    await tester.pump();
    expect(c.calls, isEmpty);
    await close(tester);
  });

  testWidgets('a failed native stop asks for «Отключить VPN»', (tester) async {
    c
      ..phase = ConnectionPhase.failed
      ..error = 'native_disconnect_failed';
    await expectScreen(
      tester,
      title: 'Требуется действие',
      label: 'Отключить VPN',
      tap: ['disconnect'],
    );
  });

  testWidgets('the orbit runs the same action as the button', (tester) async {
    await show(tester);
    await tester.tap(find.byType(OrbitBlob));
    await tester.pump();
    expect(c.calls, ['connect']);
    await close(tester);
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/main.dart';

import 'connection_test.dart' show FakeApi, FakeNative;

void main() {
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
            theme: ThemeData(brightness: brightness),
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
        await tester.scrollUntilVisible(find.text('Выйти из аккаунта'), 200);
        expect(tester.takeException(), isNull);
      }
    },
  );
  testWidgets('missing API produces explicit setup screen', (tester) async {
    await tester.pumpWidget(VpnApp(api: null, native: FakeNative()));
    expect(find.textContaining('API не настроен'), findsOneWidget);
    expect(find.text('Подключено'), findsNothing);
  });
}

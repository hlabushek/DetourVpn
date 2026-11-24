import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/kill_switch_setting.dart';
import 'package:vpn_client/settings_screen.dart';

/// ADR-196: plumbing of the Windows kill-switch setting. The native side is a MOCK channel
/// that stores the value in memory; this proves the Dart wiring only, not any firewall rule.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('project.vpn/tunnel.v1');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  late List<MethodCall> calls;
  bool? stored; // null = never written (fresh install)
  var failWrite = false;

  setUp(() {
    calls = [];
    stored = null;
    failWrite = false;
    messenger.setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      switch (call.method) {
        case 'killSwitchRead':
          return stored ?? false;
        case 'killSwitchWrite':
          if (failWrite) {
            throw PlatformException(code: 'native_operation_failed');
          }
          stored = (call.arguments as Map)['enabled'] as bool;
          return stored;
      }
      return null;
    });
  });
  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  test(
    'default is OFF on a fresh install and when native has no method',
    () async {
      expect(await const KillSwitchSetting().read(), isFalse);
      messenger.setMockMethodCallHandler(
        channel,
        null,
      ); // MissingPluginException
      expect(await const KillSwitchSetting().read(), isFalse);
      messenger.setMockMethodCallHandler(
        channel,
        (call) async =>
            throw PlatformException(code: 'native_operation_failed'),
      );
      expect(await const KillSwitchSetting().read(), isFalse);
    },
  );

  test('write passes the bool to native and the value persists', () async {
    const setting = KillSwitchSetting();
    expect(await setting.write(true), isTrue);
    expect(calls.last.method, 'killSwitchWrite');
    expect(calls.last.arguments, {'enabled': true});
    expect(await setting.read(), isTrue); // read back from the native store
    expect(await setting.write(false), isFalse);
    expect(calls.last.arguments, {'enabled': false});
    expect(await setting.read(), isFalse);
  });

  test('the error code has a Russian explanation', () {
    expect(messageFor('kill_switch_failed'), contains('блокировку трафика'));
  });

  testWidgets('settings row shows OFF by default and toggles through native', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: KillSwitchRow())),
    );
    await tester.pumpAndSettle();
    expect(find.text(KillSwitchSetting.title), findsOneWidget);
    final tile = find.byKey(const ValueKey('kill-switch'));
    expect(tester.widget<SwitchListTile>(tile).value, isFalse);
    expect(find.text(killSwitchSubtitle(false)), findsOneWidget);

    await tester.tap(tile);
    await tester.pumpAndSettle();
    expect(stored, isTrue);
    expect(tester.widget<SwitchListTile>(tile).value, isTrue);

    // A failed write keeps the shown value and says so.
    failWrite = true;
    await tester.tap(tile);
    await tester.pumpAndSettle();
    expect(stored, isTrue);
    expect(tester.widget<SwitchListTile>(tile).value, isTrue);
    expect(
      find.text('Не удалось сохранить настройку. Повторите позже.'),
      findsOneWidget,
    );
  });

  testWidgets('a persisted ON value is shown when the row opens', (
    tester,
  ) async {
    stored = true;
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: KillSwitchRow())),
    );
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<SwitchListTile>(find.byKey(const ValueKey('kill-switch')))
          .value,
      isTrue,
    );
  });
}

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/split_apps.dart';

// TEST ONLY platform channel; no proof of Android OS routing or installed apps.
void main() {
  const channel = MethodChannel('project.vpn/tunnel.v1');
  late List<String> selected;
  late String mode;
  late String lastMode;
  late bool denySave;
  late int writes;

  setUp(() {
    selected = ['org.example.saved'];
    mode = 'exclude';
    lastMode = 'exclude';
    denySave = false;
    writes = 0;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          switch (call.method) {
            case 'apps':
              return [
                {
                  'packageName': 'org.example.browser',
                  'label': 'Browser',
                  'icon': null,
                },
                {
                  'packageName': 'org.example.video',
                  'label': 'Video',
                  'icon': null,
                },
              ];
            case 'splitRead':
              return selected;
            case 'splitModeRead':
              return mode;
            case 'splitLastModeRead':
              return lastMode;
            case 'splitWrite':
              writes++;
              if (denySave) {
                throw PlatformException(code: 'native_operation_failed');
              }
              final arguments = call.arguments as Map;
              selected = List<String>.from(arguments['packages'] as List);
              mode = arguments['mode'] as String;
              if (mode == 'exclude' || mode == 'include') lastMode = mode;
              return null;
          }
          throw StateError('Unexpected TEST ONLY channel call');
        });
  });

  tearDown(
    () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null),
  );

  Future<void> mountAndOpen(WidgetTester tester) async {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => const SplitAppsScreen(),
                ),
              ),
              child: const Text('Open TEST ONLY'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open TEST ONLY'));
    await tester.pumpAndSettle();
  }

  testWidgets('search matches app label and Android package ID', (
    tester,
  ) async {
    await mountAndOpen(tester);

    await tester.enterText(find.byType(TextField), 'video');
    await tester.pumpAndSettle();
    expect(find.text('Video'), findsOneWidget);
    expect(find.text('org.example.video'), findsOneWidget);
    expect(find.text('Browser'), findsNothing);

    await tester.enterText(find.byType(TextField), 'example.browser');
    await tester.pumpAndSettle();
    expect(find.text('Browser'), findsOneWidget);
    expect(
      find.descendant(
        of: find.widgetWithText(CheckboxListTile, 'Browser'),
        matching: find.text('org.example.browser'),
      ),
      findsOneWidget,
    );
    expect(find.text('Video'), findsNothing);
  });

  testWidgets('selected applications are listed first', (tester) async {
    await mountAndOpen(tester);
    final firstTile = tester.widget<CheckboxListTile>(
      find.byType(CheckboxListTile).first,
    );
    expect((firstTile.title! as Text).data, 'org.example.saved');
  });

  testWidgets('all-traffic mode auto-saves and restores the prior selection', (
    tester,
  ) async {
    await mountAndOpen(tester);
    await tester.tap(find.byType(SwitchListTile));
    await tester.pumpAndSettle();

    expect(mode, 'all');
    expect(selected, ['org.example.saved']);
    expect(find.text('Название или код Android'), findsNothing);
    expect(
      find.text('Настройки сохраняются автоматически на телефоне.'),
      findsOneWidget,
    );

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open TEST ONLY'));
    await tester.pumpAndSettle();
    expect(
      tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
      false,
    );
    expect(selected, ['org.example.saved']);
  });

  testWidgets('mode and selected apps persist immediately after each change', (
    tester,
  ) async {
    await mountAndOpen(tester);
    await tester.ensureVisible(find.text('org.example.browser'));
    await tester.tap(find.text('org.example.browser'));
    await tester.pumpAndSettle();
    expect(selected.toSet(), {'org.example.saved', 'org.example.browser'});
    expect(writes, 1);

    await tester.tap(find.text('Через VPN только выбранные'));
    await tester.pumpAndSettle();
    expect(mode, 'include');
    expect(writes, 2);

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open TEST ONLY'));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<RadioGroup<String>>(find.byType(RadioGroup<String>))
          .groupValue,
      'include',
    );
    expect(selected.toSet(), {'org.example.saved', 'org.example.browser'});
  });

  testWidgets('include mode can be chosen before selecting an app', (
    tester,
  ) async {
    selected = [];
    await mountAndOpen(tester);
    await tester.tap(find.text('Через VPN только выбранные'));
    await tester.pumpAndSettle();

    expect(mode, 'include');
    expect(writes, 1);
    expect(
      find.text(
        'Добавь хотя бы одно приложение, чтобы подключиться в этом режиме.',
      ),
      findsOneWidget,
    );
  });

  testWidgets('native save rejection restores the last saved UI state', (
    tester,
  ) async {
    await mountAndOpen(tester);
    denySave = true;
    await tester.tap(find.text('Через VPN только выбранные'));
    await tester.pumpAndSettle();

    expect(mode, 'exclude');
    expect(
      find.text('Не удалось сохранить. Отключи VPN и попробуй снова.'),
      findsOneWidget,
    );
  });
}

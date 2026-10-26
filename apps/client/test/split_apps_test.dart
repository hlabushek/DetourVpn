import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/split_apps.dart';

// TEST ONLY platform channel; no proof of Android OS routing or installed apps.
void main() {
  const channel = MethodChannel('project.vpn/tunnel.v1');
  late List<String> selected;
  late String mode;
  late bool denySave;
  setUp(() {
    selected = ['org.example.saved'];
    mode = 'exclude';
    denySave = false;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          switch (call.method) {
            case 'apps':
              return ['org.example.other'];
            case 'splitRead':
              return selected;
            case 'splitModeRead':
              return mode;
            case 'splitWrite':
              if (denySave)
                throw PlatformException(code: 'native_operation_failed');
              final arguments = call.arguments as Map;
              selected = List<String>.from(arguments['packages'] as List);
              mode = arguments['mode'] as String;
              return null;
          }
          throw StateError('Unexpected TEST ONLY channel call');
        });
  });
  tearDown(
    () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null),
  );
  testWidgets(
    'saved missing-launcher selection is shown and local changes save',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => TextButton(
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
      );
      await tester.tap(find.text('Open TEST ONLY'));
      await tester.pumpAndSettle();
      expect(find.text('org.example.saved'), findsOneWidget);
      expect(
        tester
            .widget<CheckboxListTile>(
              find.ancestor(
                of: find.text('org.example.saved'),
                matching: find.byType(CheckboxListTile),
              ),
            )
            .value,
        true,
      );
      await tester.tap(find.text('org.example.other'));
      await tester.pump();
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(selected.toSet(), {'org.example.other', 'org.example.saved'});
      expect(mode, 'exclude');
    },
  );
  testWidgets('included-app mode saves only selected apps', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => const SplitAppsScreen()),
            ),
            child: const Text('Open TEST ONLY'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open TEST ONLY'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Через VPN только выбранные'));
    await tester.pump();
    await tester.tap(find.text('Сохранить'));
    await tester.pumpAndSettle();
    expect(mode, 'include');
    expect(selected, ['org.example.saved']);
  });
  testWidgets('included-app mode cannot save an empty allowlist', (
    tester,
  ) async {
    selected = [];
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => const SplitAppsScreen()),
            ),
            child: const Text('Open TEST ONLY'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open TEST ONLY'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Через VPN только выбранные'));
    await tester.pump();
    expect(
      find.text(
        'Для режима «только выбранные» отметь хотя бы одно приложение.',
      ),
      findsOneWidget,
    );
    expect(
      tester
          .widget<FilledButton>(find.widgetWithText(FilledButton, 'Сохранить'))
          .onPressed,
      isNull,
    );
  });
  testWidgets(
    'native routing rejection remains visible with selection intact',
    (tester) async {
      denySave = true;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => TextButton(
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
      );
      await tester.tap(find.text('Open TEST ONLY'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Сначала отключите VPN'), findsOneWidget);
      expect(selected, ['org.example.saved']);
    },
  );
}

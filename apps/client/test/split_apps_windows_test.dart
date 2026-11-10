import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/platform_info.dart';
import 'package:vpn_client/split_apps.dart';

// TEST ONLY platform channel; proves UI and channel plumbing, not Windows routing or the engine.
void main() {
  const channel = MethodChannel('project.vpn/tunnel.v1');
  late List<String> capabilities;
  late List<String> savedApps;
  late List<String> savedDomains;
  late String mode;
  late List<MethodCall> calls;
  late String? picked;

  setUp(() {
    capabilities = ['awg3', 'domain-routing.v1'];
    savedApps = [];
    savedDomains = ['*.example.ru'];
    mode = 'exclude';
    calls = [];
    picked = r'C:\Tools\Custom Tool.exe';
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          calls.add(call);
          switch (call.method) {
            case 'capabilities':
              return capabilities;
            case 'apps':
              return [
                {
                  'packageName': r'C:\Program Files\Browser\browser.exe',
                  'label': 'Browser',
                  'icon': null,
                },
              ];
            case 'splitRead':
              return savedApps;
            case 'splitDomainsRead':
              return savedDomains;
            case 'splitModeRead':
            case 'splitLastModeRead':
              return mode;
            case 'pickApp':
              return picked;
            case 'splitWrite':
              final args = call.arguments as Map;
              savedApps = List<String>.from(args['packages'] as List);
              savedDomains = List<String>.from(args['domains'] as List);
              mode = args['mode'] as String;
              return savedDomains;
          }
          throw StateError('Unexpected TEST ONLY channel call');
        });
  });

  tearDown(() {
    debugIsWindowsOverride = false;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  Future<void> open(WidgetTester tester) async {
    tester.view.physicalSize = const Size(900, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const MaterialApp(home: SplitAppsScreen()));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'without domain-routing.v1 the domain tab is hidden and rules are never read',
    (tester) async {
      capabilities = ['awg3'];
      await open(tester);
      expect(find.text('Домены'), findsNothing);
      expect(find.byType(TabBar), findsNothing);
      expect(calls.map((c) => c.method), isNot(contains('splitDomainsRead')));
      expect(
        find.textContaining('по доменам в этой бете недоступна'),
        findsOneWidget,
      );
    },
  );

  testWidgets('a failing capabilities call (Android) also hides domains', (
    tester,
  ) async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          if (call.method == 'capabilities') throw MissingPluginException();
          if (call.method == 'apps') return <Object?>[];
          if (call.method == 'splitRead') return <String>[];
          return 'exclude';
        });
    await open(tester);
    expect(find.byType(TabBar), findsNothing);
  });

  testWidgets(
    'with the capability both tabs show, domains load and save through the channel',
    (tester) async {
      debugIsWindowsOverride = true;
      await open(tester);
      expect(find.text('Домены'), findsOneWidget);
      expect(find.text('Приложения'), findsOneWidget);
      expect(find.text('*.example.ru'), findsOneWidget);
      expect(find.textContaining('зашифрованный DNS, ECH'), findsOneWidget);

      await tester.tap(find.text('Добавить домен'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, 'exact.example.net');
      await tester.tap(find.text('Добавить'));
      await tester.pumpAndSettle();
      expect(savedDomains, ['*.example.ru', 'exact.example.net']);
      expect(find.text('exact.example.net'), findsOneWidget);
    },
  );

  testWidgets('Windows picks a program by path and selects it', (tester) async {
    debugIsWindowsOverride = true;
    await open(tester);
    await tester.tap(find.text('Приложения'));
    await tester.pumpAndSettle();
    expect(find.text('Browser'), findsOneWidget);
    expect(find.byKey(const ValueKey('pick-app')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('pick-app')));
    await tester.pumpAndSettle();
    expect(savedApps, [r'C:\Tools\Custom Tool.exe']);
    expect(find.text('Custom Tool'), findsOneWidget); // file name without .exe
    expect(find.text(r'C:\Tools\Custom Tool.exe'), findsOneWidget);
  });

  testWidgets('a cancelled picker changes nothing', (tester) async {
    debugIsWindowsOverride = true;
    picked = null;
    await open(tester);
    await tester.tap(find.text('Приложения'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('pick-app')));
    await tester.pumpAndSettle();
    expect(calls.map((c) => c.method), isNot(contains('splitWrite')));
  });

  testWidgets('Android keeps the apps-only layout without a picker button', (
    tester,
  ) async {
    capabilities = [];
    await open(tester);
    expect(find.byKey(const ValueKey('pick-app')), findsNothing);
    expect(find.byType(TabBar), findsNothing);
  });
}

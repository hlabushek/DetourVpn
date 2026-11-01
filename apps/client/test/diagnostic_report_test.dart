import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/diagnostic_report.dart';
import 'package:vpn_client/native_tunnel.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Dart and Android diagnostic allowlists stay synchronized', () {
    final dartSource = File('lib/diagnostic_report.dart').readAsStringSync();
    final javaSource = File(
      'android/app/src/main/java/org/example/vpnpoc/DiagnosticReportText.java',
    ).readAsStringSync();

    String sourceBlock(String source, String pattern) {
      final match = RegExp(pattern, dotAll: true).firstMatch(source);
      expect(match, isNotNull);
      return match!.group(1)!;
    }

    Set<String> values(String source, String quote) => RegExp(
      '$quote([a-z_]+)$quote',
    ).allMatches(source).map((match) => match.group(1)!).toSet();

    final dartErrors = values(
      sourceBlock(dartSource, r'const _reportableErrors = <String>\{(.*?)\}'),
      "'",
    );
    final dartStages = values(
      sourceBlock(dartSource, r'const _reportableStages = <String>\{(.*?)\}'),
      "'",
    );
    final nativeErrors = values(
      sourceBlock(javaSource, r'ERROR_CODES = Set\.of\((.*?)\);'),
      '"',
    );
    final nativeStages = values(
      sourceBlock(javaSource, r'STAGES = Set\.of\((.*?)\);'),
      '"',
    );

    expect(nativeErrors, equals({...dartErrors, 'unknown_error'}));
    expect(nativeStages, equals({...dartStages, 'unknown'}));
  });

  test('report reduces unknown server values to fixed categories', () {
    final report = DiagnosticReport.fromError(
      'token=secret',
      'https://host/private',
    );

    expect(report.errorCode, 'unknown_error');
    expect(report.stage, 'unknown');
    expect(
      report.text,
      'DetourVpn — отчёт диагностики\nКод: unknown_error\nЭтап: unknown',
    );
  });

  test('report retains known error and local stage', () {
    final report = DiagnosticReport.fromError(
      'invalid_response',
      'lease_create',
    );
    expect(report.errorCode, 'invalid_response');
    expect(report.stage, 'lease_create');
  });

  testWidgets('diagnostic is shared only after report preview confirmation', (
    tester,
  ) async {
    final calls = <MethodCall>[];
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(PlatformTunnel.channel, (call) async {
      calls.add(call);
      return null;
    });
    addTearDown(
      () => messenger.setMockMethodCallHandler(PlatformTunnel.channel, null),
    );

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: DiagnosticReportButton(
            error: 'invalid_response',
            stage: 'lease_create',
          ),
        ),
      ),
    );
    await tester.tap(find.byKey(const Key('diagnostic-share-button')));
    await tester.pumpAndSettle();

    expect(calls, isEmpty);
    expect(
      find.text(
        'DetourVpn — отчёт диагностики\nКод: invalid_response\nЭтап: lease_create',
      ),
      findsOneWidget,
    );
    await tester.tap(find.text('Отмена'));
    await tester.pumpAndSettle();
    expect(calls, isEmpty);

    await tester.tap(find.byKey(const Key('diagnostic-share-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Выбрать приложение'));
    await tester.pumpAndSettle();

    expect(calls, hasLength(1));
    expect(calls.single.method, 'shareDiagnosticReport');
    expect(calls.single.arguments, {
      'code': 'invalid_response',
      'stage': 'lease_create',
    });
  });
}

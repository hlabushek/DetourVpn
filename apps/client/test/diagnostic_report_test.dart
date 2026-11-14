import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/diagnostic_report.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/platform_info.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'Windows copies the fixed-category report to the clipboard only',
    (tester) async {
      debugIsWindowsOverride = true;
      addTearDown(() => debugIsWindowsOverride = false);
      final calls = <MethodCall>[];
      String? clipboard;
      final messenger =
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
        if (call.method == 'Clipboard.setData') {
          clipboard = (call.arguments as Map)['text'] as String;
        }
        return null;
      });
      messenger.setMockMethodCallHandler(PlatformTunnel.channel, (call) async {
        calls.add(call);
        return null;
      });
      addTearDown(() {
        messenger.setMockMethodCallHandler(SystemChannels.platform, null);
        messenger.setMockMethodCallHandler(PlatformTunnel.channel, null);
      });

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
      expect(find.textContaining('буфер обмена'), findsOneWidget);
      expect(clipboard, isNull);
      await tester.tap(find.text('Скопировать'));
      await tester.pumpAndSettle();

      expect(calls, isEmpty); // there is no share sheet on Windows
      expect(clipboard, contains('Код: invalid_response'));
      expect(clipboard, contains('Этап: lease_create'));
      expect(clipboard, contains('Что сделать:')); // Russian hint, no "unknown"
    },
  );

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

    // Coarse context sets and Russian hint texts must also match.
    for (final name in ['HandshakeAges', 'Windows', 'NetworkChanges']) {
      final dart = values(
        sourceBlock(dartSource, 'const _reportable$name = <String>\\{(.*?)\\}'),
        "'",
      );
      final java = values(
        sourceBlock(
          javaSource,
          '${name.replaceAllMapped(RegExp('[A-Z]'), (m) => m.start == 0 ? m[0]! : '_${m[0]}').toUpperCase()} = Set\\.of\\((.*?)\\);',
        ),
        '"',
      );
      expect(java, equals(dart), reason: name);
    }
    final dartHints = <String, String>{
      for (final m in RegExp(r"'([a-z_]+)':\s*((?:'[^']*'\s*)+),").allMatches(
        sourceBlock(
          dartSource,
          r'diagnosticHints = <String, String>\{(.*?)\n\};',
        ),
      ))
        m.group(1)!: RegExp(
          r"'([^']*)'",
        ).allMatches(m.group(2)!).map((x) => x.group(1)!).join(),
    };
    final javaHintBlock = sourceBlock(
      javaSource,
      r'HINTS = java\.util\.Map\.of\((.*?)\);',
    );
    final javaHints = <String, String>{
      for (final m in RegExp(
        r'"([a-z_]+)",\s*((?:"[^"]*"\s*\+?\s*)+)',
      ).allMatches(javaHintBlock))
        m.group(1)!: RegExp(
          r'"([^"]*)"',
        ).allMatches(m.group(2)!).map((x) => x.group(1)!).join(),
    };
    expect(dartHints, isNotEmpty);
    expect(javaHints, equals(dartHints));
  });

  test('handshake age is rounded into fixed ranges', () {
    expect(handshakeAgeBucket(-1), 'never');
    expect(handshakeAgeBucket(59), 'under_1m');
    expect(handshakeAgeBucket(60), 'm1_to_5');
    expect(handshakeAgeBucket(299), 'm1_to_5');
    expect(handshakeAgeBucket(300), 'm5_to_30');
    expect(handshakeAgeBucket(1799), 'm5_to_30');
    expect(handshakeAgeBucket(1800), 'm30_to_120');
    expect(handshakeAgeBucket(7200), 'over_2h');
    expect(networkChangeBucket(null), 'none');
    expect(networkChangeBucket(59999), 'recent');
    expect(networkChangeBucket(60000), 'earlier');
    expect(windowBucket(null), 'unknown');
    expect(windowBucket(1), 'open');
    expect(windowBucket(0), 'ended');
  });

  test(
    'new stages and context are reported with a Russian hint, not unknown',
    () {
      for (final stage in [
        'window_renewal',
        'window_advance',
        'tunnel_restart',
        'tunnel_monitor',
        'network_change',
        'session_resume',
        'panel_profile',
      ]) {
        final report = DiagnosticReport.fromError(
          'renewal_too_late',
          stage,
          handshake: 'confirmed',
          handshakeAge: 'm5_to_30',
          window: 'open',
          networkChange: 'recent',
        );
        expect(report.stage, stage);
        expect(report.text, contains('Время с handshake: m5_to_30'));
        expect(report.text, contains('Окно: open'));
        expect(report.text, contains('Смена сети: recent'));
        expect(report.text, contains('окно не удалось продлить'));
        expect(report.text, isNot(contains('unknown')));
      }
    },
  );

  test('native background stages are named, never unknown', () {
    for (final stage in [
      'native_init',
      'native_deadline',
      'native_watchdog_restart',
      'native_renewal',
      'native_restore',
    ]) {
      final report = DiagnosticReport.fromError('native_down', stage);
      expect(report.stage, stage);
      expect(report.text, isNot(contains('Этап: unknown')));
    }
    expect(
      diagnosticHintKey('native_down', 'native_renewal'),
      'renewal_failed',
    );
    // A window that ran out natively reports lease_expired (window ended), not a tunnel code.
    expect(
      diagnosticHintKey('lease_expired', 'native_deadline'),
      'window_ended',
    );
    expect(
      diagnosticHintKey('native_down', 'native_watchdog_restart'),
      'tunnel_dropped',
    );
    expect(
      diagnosticHintKey('native_down', 'native_restore'),
      'tunnel_dropped',
    );
    // An unlisted native-looking stage still falls back to unknown.
    expect(
      DiagnosticReport.fromError('native_down', 'native_secret').stage,
      'unknown',
    );
  });

  test('context values outside the fixed sets are dropped', () {
    final report = DiagnosticReport.fromError(
      'native_down',
      'tunnel_monitor',
      handshakeAge: '4242s',
      window: 'https://host',
      networkChange: 'wifi-home',
    );
    expect(report.handshakeAge, isNull);
    expect(report.window, isNull);
    expect(report.networkChange, isNull);
    expect(report.text, contains('Android остановил туннель'));
  });

  test('hint key follows the code, renewal stages win over network codes', () {
    expect(
      diagnosticHintKey('vpn_revoked', 'tunnel_monitor'),
      'vpn_permission',
    );
    expect(diagnosticHintKey('session_expired', 'unknown'), 'login_needed');
    expect(
      diagnosticHintKey('network_unavailable', 'window_renewal'),
      'renewal_failed',
    );
    expect(
      diagnosticHintKey('network_unavailable', 'tunnel_monitor'),
      'network_lost',
    );
    expect(
      diagnosticHintKey('lease_expired', 'tunnel_monitor'),
      'window_ended',
    );
    expect(diagnosticHintKey('invalid_response', 'unknown'), 'general');
    for (final key in [
      'vpn_permission',
      'login_needed',
      'network_lost',
      'server_busy',
      'quota_out',
      'renewal_failed',
      'tunnel_dropped',
      'window_ended',
      'general',
    ]) {
      expect(diagnosticHints.containsKey(key), isTrue);
    }
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
      'DetourVpn — отчёт диагностики\nКод: unknown_error\nЭтап: unknown\n'
      '${diagnosticHints['general']}',
    );
  });

  test('handshake line is a fixed category or omitted (task 7)', () {
    final seen = DiagnosticReport.fromError(
      'lease_expired',
      'lease_poll',
      handshake: 'not_seen',
    );
    expect(
      seen.text.split(String.fromCharCode(10)),
      contains('Handshake: not_seen'),
    );
    final unknown = DiagnosticReport.fromError(
      'lease_expired',
      'lease_poll',
      handshake: 'peer=secret',
    );
    expect(unknown.handshake, isNull);
    expect(unknown.text.contains('Handshake'), isFalse);
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
        'DetourVpn — отчёт диагностики\nКод: invalid_response\nЭтап: lease_create\n'
        '${diagnosticHints['general']}',
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
      'hint': 'general',
    });
  });
}

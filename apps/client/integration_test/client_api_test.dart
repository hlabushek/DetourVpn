import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/quota.dart';
import 'package:vpn_client/quota_screen.dart';

// TEST ENTRYPOINT ONLY. Real HTTP/PostgreSQL/auth/native; Telegram delivery is fake.
// Test-only loopback routes exist only in test_i013_android.py, never in production.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('Flutter login and connection lifecycle against real API', (
    tester,
  ) async {
    final api = Api(Uri.parse('http://127.0.0.1:18766'));
    final native = PlatformTunnel();
    const live = bool.fromEnvironment('VPN_I013_LIVE');
    var stage = 'login_screen';
    Future<void> until(bool Function() predicate, [int seconds = 30]) async {
      final deadline = DateTime.now().add(Duration(seconds: seconds));
      while (!predicate() && DateTime.now().isBefore(deadline)) {
        await tester.pump(const Duration(milliseconds: 250));
        await Future<void>.delayed(const Duration(milliseconds: 250));
      }
      if (!predicate()) throw StateError('i013_stage_$stage');
    }

    Future<void> tap(String text) async {
      final finder = find.text(text);
      await until(() => finder.evaluate().isNotEmpty);
      if (text == 'Подключить') {
        await until(
          () =>
              tester
                  .widget<FilledButton>(find.widgetWithText(FilledButton, text))
                  .onPressed !=
              null,
        );
      }
      await tester.ensureVisible(finder);
      await tester.tap(finder);
      await tester.pump();
    }

    Future<String?> egress() async {
      // A new normally verified TLS connection after each route transition.
      final client = HttpClient()
        ..connectionTimeout = const Duration(seconds: 4);
      try {
        final request = await client
            .getUrl(Uri.parse('https://api.ipify.org'))
            .timeout(const Duration(seconds: 5));
        request.followRedirects = false;
        final response = await request.close().timeout(
          const Duration(seconds: 5),
        );
        if (response.statusCode != 200) return null;
        final bytes = await response
            .fold<List<int>>([], (a, b) => a..addAll(b))
            .timeout(const Duration(seconds: 5));
        if (bytes.length > 64) return null;
        return String.fromCharCodes(bytes).trim();
      } catch (_) {
        return null;
      } finally {
        client.close(force: true);
      }
    }

    try {
      await tester.pumpWidget(VpnApp(api: api, native: native));
      await tap('Войти через Telegram');
      stage = 'login_challenge';
      await until(() => find.text('Подтвердить вход').evaluate().isNotEmpty);
      final issued = await api.call('POST', '/api/v1/__i013__/issue');
      await tester.enterText(find.byType(TextField), issued['code'] as String);
      await tap('Подтвердить вход');
      stage = 'login_confirm';
      await until(() => find.text('Подключить').evaluate().isNotEmpty);
      if (const bool.fromEnvironment('VPN_I015_LOCAL')) {
        expect(live, false);
        stage = 'reward_claim';
        final config = await api.call('POST', '/api/v1/__i015__/config');
        final receipt = await api.call('POST', '/api/v1/rewards/claim', config);
        expect(receipt['reward_bytes'], 17); // TEST ONLY synthetic reward.
        expect(
          await api.call('POST', '/api/v1/rewards/claim', config),
          receipt,
        );
        final expected = await api.call('POST', '/api/v1/__i015__/verify');
        stage = 'reward_history';
        await tap('Трафик и история');
        await until(() => find.byType(QuotaScreen).evaluate().isNotEmpty);
        await tester.pumpAndSettle();
        QuotaController quota() =>
            tester.widget<QuotaScreen>(find.byType(QuotaScreen)).controller;
        await until(
          () =>
              !quota().loading &&
              quota().snapshot?.revision == expected['revision'],
        );
        expect(quota().snapshot!.available, expected['available_bytes']);
        expect(quota().entries.length, 2);
        final rewards = quota().entries.where(
          (entry) => entry.source == 'reward',
        );
        expect(rewards.length, 1);
        expect(rewards.single.delta, 17);
        await tester.scrollUntilVisible(
          find.text('Бонусный трафик'),
          300,
          scrollable: find.descendant(
            of: find.byType(QuotaScreen),
            matching: find.byType(Scrollable),
          ),
        );
        expect(find.text('Бонусный трафик'), findsOneWidget);
        stage = 'reward_replay_refresh';
        expect(
          await api.call('POST', '/api/v1/rewards/claim', config),
          receipt,
        );
        await quota().refresh();
        await tester.pump();
        expect(quota().snapshot!.available, expected['available_bytes']);
        expect(quota().snapshot!.revision, expected['revision']);
        expect(
          quota().entries.where((entry) => entry.source == 'reward').length,
          1,
        );
        await api.call('POST', '/api/v1/__i015__/verify');
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'reward_history_and_replay',
        });
        await tester.pageBack();
        await tester.pump();
      }
      // Optional I-014 local-only acceptance; endpoint exists only in pytest.
      // Normal I-013 runs do not have this route.
      Map<String, dynamic>? seeded;
      if (const bool.fromEnvironment('VPN_I014_LOCAL')) {
        expect(live, false);
        seeded = await api.call('POST', '/api/v1/__i014__/seed');
      }
      if (seeded != null) {
        stage = 'quota_open';
        await tap('Трафик и история');
        await until(() => find.byType(QuotaScreen).evaluate().isNotEmpty);
        // Complete the route transition before dragging its scrollable. During
        // the animation both the home list and history list are onstage.
        await tester.pumpAndSettle();
        final quotaScroll = find.descendant(
          of: find.byType(QuotaScreen),
          matching: find.byType(Scrollable),
        );
        QuotaController quota() =>
            tester.widget<QuotaScreen>(find.byType(QuotaScreen)).controller;
        stage = 'quota_summary';
        await until(
          () =>
              !quota().loading &&
              quota().snapshot?.revision == seeded!['revision'],
        );
        expect(quota().snapshot!.available, seeded['available_bytes']);
        stage = 'quota_text';
        expect(
          find.text('Доступно: ${byteText(seeded['available_bytes'] as int)}'),
          findsOneWidget,
        );
        expect(quota().entries.length, 20);
        stage = 'quota_expected_api';
        final expected = await api.call(
          'GET',
          '/api/v1/quota/history?limit=20&snapshot_revision=25&before_sequence=6',
        );
        stage = 'quota_scroll';
        await tester.scrollUntilVisible(
          find.text('Показать ещё'),
          500,
          scrollable: quotaScroll,
        );
        stage = 'quota_more_tap';
        await tap('Показать ещё');
        stage = 'quota_more_wait';
        await until(() => !quota().loading && quota().entries.length == 25);
        stage = 'quota_more_compare';
        expect(
          quota().entries.last.delta,
          (expected['items'] as List).last['delta_bytes'],
        );
        expect(quota().nextBefore, null);
        stage = 'quota_retry';
        // The seed endpoint is repeat-safe: refresh must not double the balance.
        await api.call('POST', '/api/v1/__i014__/seed');
        await tester.scrollUntilVisible(
          find.text('Обновить трафик и историю'),
          -500,
          scrollable: quotaScroll,
        );
        await tap('Обновить трафик и историю');
        await until(() => !quota().loading);
        expect(quota().snapshot!.available, seeded['available_bytes']);
        expect(quota().entries.length, 20);
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'quota_ui_exact_and_paginated',
        });
        await tester.pageBack();
        await tester.pump();
      }
      await api.call('POST', '/api/v1/__i013__/fact', {
        'name': 'flutter_auth_ui',
      });
      final baseline = live ? await egress() : null;
      if (live) expect(baseline != null && baseline != '45.10.166.106', true);
      await tap('Подключить');
      stage = 'connect';
      if (!live) {
        stage = 'admission_closed';
        await until(
          () => find.text('Требуется действие').evaluate().isNotEmpty,
        );
        expect((await native.snapshot()).up, false);
        expect(find.text('Подключено'), findsNothing);
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'admission_closed_no_false_connected',
        });
      } else {
        await until(() => find.text('Подключено').evaluate().isNotEmpty);
        expect((await native.snapshot()).confirmed, true);
        expect(await egress() == '45.10.166.106', true);
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'flutter_handshake_and_staging_egress',
        });
        await api.call('POST', '/api/v1/__i013__/background');
        await tester.pump();
        expect((await native.snapshot()).up, true);
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'background_foreground_preserves_lease',
        });
      }
      stage = 'disconnect';
      await tap('Отключить VPN');
      await until(() => find.text('Отключено').evaluate().isNotEmpty);
      expect((await native.snapshot()).up, false);
      await api.call('POST', '/api/v1/__i013__/settled');
      if (live) {
        String? restored;
        for (var attempt = 0; attempt < 6; attempt++) {
          restored = await egress();
          if (restored != null && restored != '45.10.166.106') break;
          await Future<void>.delayed(const Duration(seconds: 1));
        }
        expect(restored != null && restored != '45.10.166.106', true);
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'disconnect_restores_network',
        });
        // A second independent lease: server API revoke must reach the UI/native.
        await tap('Подключить');
        await until(() => find.text('Подключено').evaluate().isNotEmpty);
        await api.call('POST', '/api/v1/__i013__/revoke');
        await until(
          () => find.text('Требуется действие').evaluate().isNotEmpty,
        );
        expect((await native.snapshot()).up, false);
        await api.call('POST', '/api/v1/__i013__/settled');
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'remote_revoke_stops_flutter_vpn',
        });
        // A third lease expires without user action; native owner enforces local stop.
        await tap('Подключить');
        await until(() => find.text('Подключено').evaluate().isNotEmpty);
        await until(
          () => find.text('Требуется действие').evaluate().isNotEmpty,
          75,
        );
        expect((await native.snapshot()).up, false);
        await api.call('POST', '/api/v1/__i013__/settled');
        await api.call('POST', '/api/v1/__i013__/fact', {
          'name': 'lease_expiry_stops_flutter_vpn',
        });
      }
    } catch (_, stack) {
      // Sanitized stage only; never include widget tree, login code or tokens.
      final line =
          RegExp(
            r'client_api_test.dart:(\d+)',
          ).firstMatch(stack.toString())?.group(1) ??
          '0';
      throw StateError('i013_stage_$stage i013_line_$line');
    } finally {
      await native.disconnect();
    }
  });
}

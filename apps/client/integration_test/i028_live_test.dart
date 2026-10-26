import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/session_store.dart';
import 'package:vpn_client/window_connection.dart';

// TEST ENTRYPOINT ONLY. Real Android/API/PG/AWG; fake Telegram and test quota.
// Run seed and run in distinct Android processes with --no-uninstall.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('I-028 bounded real v3 staging', (tester) async {
    await tester.runAsync(() async {
      const phase = String.fromEnvironment('VPN_I028_PHASE');
      final api = Api(
        Uri.parse('http://127.0.0.1:18766'),
        store: PlatformSessionStore(),
      );
      final native = PlatformTunnel();
      WindowConnectionController? controller;
      var seedSaved = false;
      var splitOwned = false;
      var stage = 'start';
      var naturalCompleted = false;
      Future<void> naturalStage(String value) async {
        if (phase == 'natural_sleep') {
          // Test-only, app-private status in Android's no-backup data folder.
          // Never write credentials, probe data, or raw exceptions.
          await PlatformTunnel.channel.invokeMethod<void>('i028StageWrite', {
            'value': value,
          });
        }
      }

      Future<void> recordNaturalTunnelSnapshot(NativeSnapshot snapshot) async {
        if (phase != 'natural_sleep') return;
        try {
          await PlatformTunnel.channel
              .invokeMethod<void>('i028TunnelSnapshot', {
                'up': snapshot.up,
                'network': snapshot.network,
                'handshakeAge': snapshot.handshakeAge,
                'remainingMs': snapshot.remainingMs,
              });
        } catch (_) {
          // Bounded diagnostics must never change the acceptance behavior.
        }
      }

      void check(bool value) {
        if (!value) throw StateError('i028_stage_$stage');
      }

      Future<void> fact(String name) async {
        await api.call('POST', '/api/v1/__i028__/fact', {'name': name});
      }

      Future<Map<String, dynamic>> action(String name) async {
        await api.call('POST', '/api/v1/__i028__/action/$name');
        for (var i = 0; i < 300; i++) {
          await Future<void>.delayed(const Duration(milliseconds: 500));
          final result = await api.call('GET', '/api/v1/__i028__/job');
          if (result['done'] == true) {
            check(result['ok'] == true);
            return result;
          }
        }
        throw StateError('i028_stage_$stage');
      }

      Future<void> connected() async {
        await controller!.refreshNodes();
        final available = controller!.nodes.where(
          (n) => n.reason == 'available',
        );
        check(available.length == 1);
        controller.selectNode(available.single.id);
        await controller.connect();
        for (var i = 0; i < 20; i++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller!.refresh();
          if ((await native.snapshot()).confirmed) return;
        }
        check(false);
      }

      Future<void> closed() async {
        await controller!.disconnect();
        for (var i = 0; i < 30 && controller!.hasLease; i++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller!.refresh();
        }
        check(!controller!.hasLease && !(await native.snapshot()).up);
      }

      try {
        await naturalStage('start');
        check(const bool.fromEnvironment('VPN_WINDOW_TUNNELS'));
        if (phase == 'cleanup') {
          await api.forgetSession();
          await PlatformTunnel.channel.invokeMethod<void>('splitWrite', {
            'packages': <String>[],
          });
          return;
        }
        if (phase == 'seed') {
          stage = 'seed';
          check(await PlatformSessionStore().read() == null);
          check(
            (await PlatformTunnel.channel.invokeListMethod<String>(
              'splitRead',
            ))!.isEmpty,
          );
          final start = await api.call('POST', '/api/v1/auth/start');
          final issued = await api.call('POST', '/api/v1/__i028__/issue');
          await api.acceptSession(
            await api.call('POST', '/api/v1/auth/verify', {
              'request_id': start['request_id'],
              'verifier': start['verifier'],
              'code': issued['code'],
            }),
          );
          await api.call('POST', '/api/v1/devices', {
            'platform': 'android',
            'key_type': 'x25519',
            'public_key': await native.publicKey(),
          });
          await api.call('POST', '/api/v1/__i028__/seed', {'pid': pid});
          check(await PlatformSessionStore().read() != null);
          seedSaved = true;
          return; // Deliberately retain encrypted session for the next process.
        }
        check(
          phase == 'run' ||
              phase == 'lifecycle' ||
              phase == 'network_revoke' ||
              phase == 'natural_sleep',
        );
        check(
          (await PlatformTunnel.channel.invokeListMethod<String>(
            'splitRead',
          ))!.isEmpty,
        );
        splitOwned = true;
        stage = 'restore_real_account';
        await api.restoreSession();
        check(api.authenticated);
        await api.call('POST', '/api/v1/__i028__/restored', {'pid': pid});
        await fact('real_account_refresh_after_android_process_restart');
        final key = await native.publicKey();
        stage = 'baseline_probe';
        final baseline = await action('probe_all');
        check(
          baseline['active_network_is_vpn'] == false &&
              baseline['egress_is_staging'] == false,
        );
        controller = WindowConnectionController(api, native);
        if (phase == 'natural_sleep') {
          stage = 'natural_sleep_connect';
          await naturalStage(stage);
          await connected();
          check((await action('probe_ipv4'))['egress_is_staging'] == true);
          await recordNaturalTunnelSnapshot(await native.snapshot());
          await api.call('POST', '/api/v1/__i028__/action/natural_sleep');
          stage = 'natural_sleep_wait';
          await naturalStage(stage);
          // USB is intentionally absent during this interval. No local API call
          // is attempted until the operator reconnects the device.
          await Future<void>.delayed(const Duration(minutes: 8));
          stage = 'natural_sleep_wake';
          await naturalStage(stage);
          final afterSleep = await native.snapshot();
          await recordNaturalTunnelSnapshot(afterSleep);
          check(!afterSleep.up);
          await fact('natural_sleep_native_down_after_wake');
          Map<String, dynamic>? observation;
          for (var i = 0; i < 20; i++) {
            try {
              final value = await api.call('GET', '/api/v1/__i028__/job');
              if (value['done'] == true) {
                observation = value;
                break;
              }
            } catch (_) {
              // The USB reverse route may still be restoring after wake.
            }
            await Future<void>.delayed(const Duration(seconds: 3));
          }
          check(
            observation != null &&
                observation['ok'] == true &&
                observation['usb_absent_confirmed'] == true &&
                observation['natural_idle_observed'] == true &&
                observation['server_closed_during_sleep'] == true,
          );
          await controller.refresh().timeout(const Duration(seconds: 15));
          await closed().timeout(const Duration(seconds: 65));
          stage = 'natural_sleep_recovery';
          await naturalStage(stage);
          await connected();
          check((await action('probe_ipv4'))['egress_is_staging'] == true);
          await fact('natural_sleep_new_staging_egress_after_recovery');
          await closed();
          await fact('natural_sleep_window_cutoff_and_real_recovery');
          await api.call('POST', '/api/v1/__i028__/verify');
          await api.call('POST', '/api/v1/auth/logout');
          naturalCompleted = true;
          return;
        }
        if (phase == 'lifecycle' || phase == 'network_revoke') {
          stage = 'lifecycle_ipv6_contrast';
          await connected();
          final ipv6Contrast = await action('probe_all');
          check(
            ipv6Contrast['egress_is_staging'] == true &&
                ipv6Contrast['ipv6_available'] == false,
          );
          if (baseline['ipv6_available'] == true) {
            await fact('baseline_ipv6_available_included_ipv6_blocked');
          }
          await closed();
          for (final mode
              in phase == 'lifecycle'
                  ? ['background', 'screen_off', 'forced_doze']
                  : <String>[]) {
            stage = mode;
            await connected();
            check((await action('probe_ipv4'))['egress_is_staging'] == true);
            check((await action(mode))['sleep_done'] == true);
            await Future<void>.delayed(const Duration(seconds: 2));
            await controller.refresh();
            final woke = await native.snapshot();
            check(!woke.up || woke.remainingMs > 0);
            await closed();
            await connected();
            check((await action('probe_ipv4'))['egress_is_staging'] == true);
            await closed();
            await fact('${mode}_window_safe_and_real_recovery');
          }
          stage = 'wifi_mobile_transition';
          await connected();
          final wifiBefore = await action('probe_all');
          check(
            wifiBefore['active_transport_wifi'] == true &&
                wifiBefore['egress_is_staging'] == true,
          );
          final mobile = await action('mobile');
          check(
            mobile['active_transport_cellular'] == true &&
                mobile['egress_is_staging'] == true,
          );
          stage = 'temporary_network_loss';
          final lost = await action('loss');
          check(lost['both_radios_disabled'] == true);
          final offline = await native.snapshot();
          check(!offline.up || offline.remainingMs > 0);
          final mobileAgain = await action('mobile');
          check(
            mobileAgain['active_transport_cellular'] == true &&
                mobileAgain['egress_is_staging'] == true,
          );
          final wifiAgain = await action('wifi');
          check(
            wifiAgain['active_transport_wifi'] == true &&
                wifiAgain['egress_is_staging'] == true,
          );
          await closed();
          await fact('wifi_mobile_loss_wifi_real_transfer_and_valid_windows');
          final mobileDirect = await action('mobile');
          check(
            mobileDirect['active_transport_cellular'] == true &&
                mobileDirect['ipv4_available'] == true &&
                mobileDirect['active_network_is_vpn'] == false,
          );
          if (mobileDirect['ipv6_available'] == true) {
            await fact('mobile_direct_ipv6_baseline_available');
          }
          await action('wifi');
          stage = 'system_settings_revoke';
          await connected();
          await action('open_vpn_settings');
          var revoked = false;
          for (var i = 0; i < 150; i++) {
            await Future<void>.delayed(const Duration(seconds: 1));
            if (!(await native.snapshot()).up &&
                (await action('permission_revoked'))['permission_revoked'] ==
                    true) {
              revoked = true;
              break;
            }
          }
          check(revoked);
          stage = 'system_settings_revoke_close';
          await closed().timeout(const Duration(seconds: 65));
          stage = 'system_settings_revoke_no_restart';
          for (var i = 0; i < 40; i++) {
            await Future<void>.delayed(const Duration(seconds: 1));
            await controller.refresh().timeout(const Duration(seconds: 10));
            check(!(await native.snapshot()).up && !controller.hasLease);
          }
          stage = 'system_settings_revoke_verify';
          await fact('system_settings_revoke_closes_without_self_restart');
          await api.call('POST', '/api/v1/__i028__/verify');
          await api.call('POST', '/api/v1/auth/logout');
          return;
        }
        stage = 'connect';
        await connected();
        await fact('native_reports_fresh_handshake');
        stage = 'included_probe';
        final included = await action('probe_all');
        check(
          included['ipv4_available'] == true &&
              included['egress_is_staging'] == true &&
              included['fixed_udp_dns_response'] == true &&
              included['ipv6_available'] == false,
        );
        await fact('included_probe_real_staging_ipv4_and_udp_dns');
        if (baseline['ipv6_available'] == true) {
          await fact('baseline_ipv6_available_included_ipv6_blocked');
        }
        stage = 'sustained_renewal';
        final watch = Stopwatch()..start();
        var nextTransfer = 20;
        while (watch.elapsed < const Duration(seconds: 185)) {
          await Future<void>.delayed(const Duration(seconds: 5));
          await controller.refresh();
          final snapshot = await native.snapshot();
          await api.call('POST', '/api/v1/__i028__/native_observation', {
            'up': snapshot.up,
            'network': snapshot.network,
            'handshake_age': snapshot.handshakeAge,
            'remaining_ms': snapshot.remainingMs,
          });
          // Idle handshake freshness and renewable-window validity are separate.
          // Preserve the production UI threshold; require actual transfer here.
          check(snapshot.up && snapshot.network && snapshot.remainingMs > 0);
          if (watch.elapsed.inSeconds >= nextTransfer) {
            final transfer = await action('probe_ipv4');
            check(transfer['egress_is_staging'] == true);
            nextTransfer = watch.elapsed.inSeconds + 20;
          }
        }
        final sustained = await action('probe_ipv4');
        check(
          sustained['egress_is_staging'] == true &&
              await native.publicKey() == key,
        );
        await api.call('POST', '/api/v1/__i028__/renewals');
        await fact('sustained_real_renewals_over_three_minutes');
        stage = 'disconnect';
        await closed();
        await fact('disconnect_native_down_and_server_closed');
        stage = 'excluded_probe';
        await PlatformTunnel.channel.invokeMethod<void>('splitWrite', {
          'packages': ['org.example.vpnpoc'],
        });
        await connected();
        final excluded = await action('probe_ipv4');
        check(excluded['egress_is_staging'] == false);
        if (baseline['ipv4_available'] == true &&
            excluded['ipv4_available'] == true) {
          await fact('same_probe_uid_included_then_excluded_egress');
        } else {
          await fact('direct_ipv4_comparison_unavailable');
        }
        await closed();
        await PlatformTunnel.channel.invokeMethod<void>('splitWrite', {
          'packages': <String>[],
        });
        stage = 'remote_revoke';
        await connected();
        await api.call('POST', '/api/v1/__i028__/revoke');
        for (var i = 0; i < 30; i++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller.refresh();
          if (!(await native.snapshot()).up && !controller.hasLease) break;
        }
        check(!(await native.snapshot()).up && !controller.hasLease);
        await fact('remote_revoke_stops_actual_android_tunnel');
        stage = 'combined_outage';
        await connected();
        await action('outage');
        await Future<void>.delayed(const Duration(seconds: 100));
        check(!(await native.snapshot()).up);
        final expired = await action('expired');
        check(expired['kernel_closed'] == true);
        await fact('api_agent_loss_native_and_kernel_deadlines_close');
        stage = 'recovery';
        await action('recover');
        for (var i = 0; i < 30 && controller.hasLease; i++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller.refresh();
        }
        check(!controller.hasLease);
        await connected();
        check((await action('probe_ipv4'))['egress_is_staging'] == true);
        await fact('agent_restart_reconciles_before_new_real_tunnel');
        await closed();
        stage = 'disconnect_renewal_race';
        await connected();
        await api.call('POST', '/api/v1/__i028__/renewal-race/arm');
        var arrived = false;
        for (var i = 0; i < 200; i++) {
          final status = await api.call(
            'POST',
            '/api/v1/__i028__/renewal-race/status',
          );
          if (status['arrived'] == true) {
            arrived = true;
            break;
          }
          await Future<void>.delayed(const Duration(milliseconds: 200));
        }
        check(arrived);
        final stopping = controller.disconnect();
        await Future<void>.delayed(const Duration(milliseconds: 300));
        check(!(await native.snapshot()).up);
        await api.call('POST', '/api/v1/__i028__/renewal-race/release');
        await stopping;
        await closed(); // Idempotent repeat while the node settles the real renewal.
        for (var i = 0; i < 10; i++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller.refresh();
          check(!(await native.snapshot()).up && !controller.hasLease);
        }
        await api.call('POST', '/api/v1/__i028__/renewal-race/verify');
        await fact('disconnect_during_real_renewal_response_cannot_reopen');
        await api.call('POST', '/api/v1/__i028__/verify');
        await api.call('POST', '/api/v1/auth/logout');
      } catch (_) {
        try {
          await naturalStage('failed_$stage');
        } catch (_) {
          /* Best effort. */
        }
        throw StateError('i028_stage_$stage');
      } finally {
        try {
          controller?.dispose();
          await native.disconnect();
          if (splitOwned) {
            await PlatformTunnel.channel.invokeMethod<void>('splitWrite', {
              'packages': <String>[],
            });
          }
          if (phase != 'seed' || !seedSaved) {
            await api.forgetSession();
          }
          api.close();
          if (naturalCompleted) await naturalStage('complete');
        } catch (_) {
          try {
            await naturalStage('failed_cleanup');
          } catch (_) {
            /* Best effort. */
          }
          rethrow;
        }
      }
    });
  }, timeout: const Timeout(Duration(minutes: 14)));
}

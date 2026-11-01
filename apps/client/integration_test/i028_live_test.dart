import 'dart:async';
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

      Future<void> recordDiagnosticSnapshot(String kind, String point) async {
        try {
          final snapshot = await native.snapshot().timeout(
            const Duration(seconds: 5),
          );
          await api
              .call('POST', '/api/v1/__i028__/native_${kind}_$point', {
                'up': snapshot.up,
                'network': snapshot.network,
                'handshake_age': snapshot.handshakeAge,
                'remaining_ms': snapshot.remainingMs,
              })
              .timeout(const Duration(seconds: 5));
        } catch (_) {
          // Diagnostics must not change the acceptance result.
        }
      }

      Future<Map<String, dynamic>> action(String name) async {
        if (name == 'egress_dns_observe') stage = 'fixed_dns_queue';
        await api.call('POST', '/api/v1/__i028__/action/$name');
        if (name == 'egress_dns_observe') stage = 'fixed_dns_wait';
        for (var i = 0; i < 300; i++) {
          await Future<void>.delayed(const Duration(milliseconds: 500));
          final result = await api.call('GET', '/api/v1/__i028__/job');
          if (result['done'] == true) {
            if (name == 'egress_dns_observe') stage = 'fixed_dns_result';
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
              phase == 'egress' ||
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
              baseline['egress_is_staging'] == false &&
              (phase != 'egress' ||
                  baseline['ipv6_literal_egress_port_control_connected'] ==
                      true),
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
        if (phase == 'egress')
          await recordDiagnosticSnapshot('egress', 'before');
        final included = await action(
          phase == 'egress' ? 'egress_probe_all' : 'probe_all',
        );
        if (phase == 'egress')
          await recordDiagnosticSnapshot('egress', 'after');
        if (phase == 'egress') {
          stage = 'fixed_dns_path_observation';
          final observation = await action('egress_dns_observe');
          check(observation['diagnostic_collected'] == true);
          stage = 'included_probe';
        }
        final includedProbePassed =
            included['ipv4_available'] == true &&
            included['egress_is_staging'] == true &&
            included['fixed_udp_dns_response'] == true &&
            included['ipv6_available'] == false &&
            included['ipv6_literal_connected'] == false &&
            included['ipv6_literal_connect_attempted'] == true &&
            (included['ipv6_literal_failure'] == 'route' ||
                included['ipv6_literal_failure'] == 'io');
        final deferDnsTimeoutForCapture =
            phase == 'egress' &&
            included['probe_complete'] == true &&
            included['process_uid_matches_target'] == true &&
            included['active_network_is_vpn'] == true &&
            included['active_network_validated'] == true &&
            included['ipv4_available'] == true &&
            included['egress_is_staging'] == true &&
            included['fixed_udp_dns_response'] == false &&
            included['dns_failure'] == 'timeout' &&
            included['ipv6_available'] == false &&
            included['ipv6_failure'] == 'dns';
        // This exact timeout is inconclusive, not a pass. Keep the verified
        // staging tunnel through the existing bounded packet-capture hold so
        // an operator capture cannot drift into post-disconnect traffic.
        // Every other failed probe still stops immediately.
        if (!includedProbePassed && !deferDnsTimeoutForCapture) check(false);
        if (includedProbePassed) {
          await fact('included_probe_real_staging_ipv4_and_udp_dns');
          if (phase == 'egress') await fact('included_ipv6_literal_blocked');
        }
        if (phase == 'egress') {
          if (controller.trafficRenewalRequested) {
            await fact('advisory_traffic_renewal_requested');
          }
          stage = 'egress_renewal_check';
          const renewalAckDeadline = Duration(seconds: 30);
          final renewalAckClock = Stopwatch()..start();
          Future<T> beforeRenewalAckDeadline<T>(Future<T> operation) {
            final remaining = renewalAckDeadline - renewalAckClock.elapsed;
            if (remaining <= Duration.zero) {
              throw TimeoutException('i028_egress_renewal_ack_timeout');
            }
            return operation.timeout(remaining);
          }

          var renewal = await beforeRenewalAckDeadline(
            api.call('GET', '/api/v1/__i028__/egress-renewal-status'),
          );
          if (controller.trafficRenewalRequested ||
              renewal['renewal_seen'] == true) {
            stage = 'egress_initial_renewal_ack_wait';
            var acknowledged = false;
            while (renewalAckClock.elapsed < renewalAckDeadline) {
              await beforeRenewalAckDeadline(controller.refresh());
              renewal = await beforeRenewalAckDeadline(
                api.call('GET', '/api/v1/__i028__/egress-renewal-status'),
              );
              if (renewal['acknowledged'] == true &&
                  controller.phase.name == 'connected') {
                acknowledged = true;
                break;
              }
              final remaining = renewalAckDeadline - renewalAckClock.elapsed;
              if (remaining > Duration.zero) {
                final pause = remaining < const Duration(milliseconds: 500)
                    ? remaining
                    : const Duration(milliseconds: 500);
                await Future<void>.delayed(pause);
              }
            }
            if (!acknowledged) {
              stage = renewal['acknowledged'] == true
                  ? 'egress_initial_client_not_connected'
                  : 'egress_initial_server_ack_missing';
            }
            check(acknowledged);
          }
          // The Linux AP capture starts before this test and waits for the
          // first outer VPN packet, then observes 90 seconds. Keep this real
          // tunnel up beyond that bounded window so the capture cannot silently
          // include post-disconnect phone traffic as if it were included VPN.
          stage = 'egress_capture_hold';
          final captureHold = Stopwatch()..start();
          var holdRefreshProbeAttempted = false;
          var inboundCounterAvailable = false;
          var previousReceivedBytes = (await native.snapshot()).receivedBytes;
          int? lastInboundProgressMs;
          var holdProbeAttempted = false;
          var holdProbeSucceeded = false;
          final holdObservations = <Map<String, Object>>[];
          while (captureHold.elapsed < const Duration(seconds: 140)) {
            await Future<void>.delayed(const Duration(seconds: 5));
            await controller.refresh().timeout(const Duration(seconds: 8));
            var snapshot = await native.snapshot().timeout(
              const Duration(seconds: 5),
            );
            check(snapshot.up && snapshot.network && snapshot.remainingMs > 0);
            if (!holdProbeAttempted &&
                captureHold.elapsed >= const Duration(seconds: 35)) {
              holdProbeAttempted = true;
              try {
                final probe = await action('probe_ipv4');
                holdProbeSucceeded = probe['egress_is_staging'] == true;
                if (holdProbeSucceeded) {
                  await controller.refresh().timeout(
                    const Duration(seconds: 8),
                  );
                  snapshot = await native.snapshot().timeout(
                    const Duration(seconds: 5),
                  );
                }
              } catch (_) {
                // This optional fixed probe only supplies inbound test traffic.
                // The scoped Egress acceptance remains based on its required probe.
              }
            }
            final receivedBytes = snapshot.receivedBytes;
            final elapsedMs = captureHold.elapsedMilliseconds;
            if (receivedBytes == null || receivedBytes < 0) {
              inboundCounterAvailable = false;
              previousReceivedBytes = null;
              lastInboundProgressMs = null;
            } else {
              inboundCounterAvailable = true;
              final previous = previousReceivedBytes;
              if (previous == null || receivedBytes < previous) {
                lastInboundProgressMs = null;
              } else if (receivedBytes > previous) {
                lastInboundProgressMs = elapsedMs;
              }
              previousReceivedBytes = receivedBytes;
            }
            final progressAtMs = lastInboundProgressMs;
            final inboundProgressRecent =
                progressAtMs != null &&
                elapsedMs >= progressAtMs &&
                elapsedMs - progressAtMs <=
                    const Duration(seconds: 60).inMilliseconds;
            renewal = await api
                .call('GET', '/api/v1/__i028__/egress-renewal-status')
                .timeout(const Duration(seconds: 8));
            if (holdObservations.length < 32) {
              holdObservations.add({
                'elapsed_seconds': elapsedMs ~/ 1000,
                'client_phase': controller.phase.name,
                'client_has_error': controller.error != null,
                'native_up': snapshot.up,
                'native_network': snapshot.network,
                'handshake_age': snapshot.handshakeAge,
                'remaining_ms': snapshot.remainingMs,
                'received_counter_available': inboundCounterAvailable,
                'inbound_progress_recent': inboundProgressRecent,
                'renewal_acknowledged': renewal['acknowledged'] == true,
                'traffic_counter_available':
                    snapshot.trafficBytes != null &&
                    snapshot.trafficBytes! >= 0,
                'traffic_renewal_requested': controller.trafficRenewalRequested,
                'renewal_pending': controller.renewalPending,
              });
            }
            if (renewal['renewal_seen'] == true &&
                (renewal['acknowledged'] != true ||
                    (controller.phase.name != 'connected' &&
                        !holdRefreshProbeAttempted))) {
              stage = 'egress_hold_renewal_ack_wait';
              final holdAckClock = Stopwatch()..start();
              var holdRenewalAcknowledged = false;
              while (holdAckClock.elapsed < renewalAckDeadline) {
                final remaining = renewalAckDeadline - holdAckClock.elapsed;
                await controller.refresh().timeout(remaining);
                renewal = await api
                    .call('GET', '/api/v1/__i028__/egress-renewal-status')
                    .timeout(remaining);
                if (renewal['acknowledged'] == true &&
                    controller.phase.name != 'connected' &&
                    !holdRefreshProbeAttempted) {
                  stage = 'egress_hold_refresh_probe';
                  if (holdObservations.isNotEmpty) {
                    try {
                      await api
                          .call(
                            'POST',
                            '/api/v1/__i028__/egress-hold-observation',
                            {
                              'fixed_probe_attempted': holdProbeAttempted,
                              'fixed_probe_succeeded': holdProbeSucceeded,
                              'observations': holdObservations,
                            },
                          )
                          .timeout(const Duration(seconds: 5));
                    } catch (_) {
                      // Save the last bounded state before the tunnel probe.
                    }
                  }
                  final probe = await action('probe_ipv4').timeout(remaining);
                  if (probe['egress_is_staging'] != true &&
                      holdObservations.isNotEmpty) {
                    try {
                      await api
                          .call(
                            'POST',
                            '/api/v1/__i028__/egress-hold-observation',
                            {
                              'fixed_probe_attempted': true,
                              'fixed_probe_succeeded': false,
                              'observations': holdObservations,
                            },
                          )
                          .timeout(const Duration(seconds: 5));
                    } catch (_) {
                      // Preserve only bounded booleans if the physical probe fails.
                    }
                  }
                  check(probe['egress_is_staging'] == true);
                  holdRefreshProbeAttempted = true;
                  try {
                    await controller.refresh().timeout(remaining);
                    final refreshed = await native.snapshot().timeout(
                      const Duration(seconds: 5),
                    );
                    await api
                        .call(
                          'POST',
                          '/api/v1/__i028__/egress-hold-observation',
                          {
                            'client_phase': controller.phase.name,
                            'client_has_error': controller.error != null,
                            'native_up': refreshed.up,
                            'native_network': refreshed.network,
                            'handshake_age': refreshed.handshakeAge,
                            'remaining_ms': refreshed.remainingMs,
                            'renewal_acknowledged':
                                renewal['acknowledged'] == true,
                          },
                        )
                        .timeout(const Duration(seconds: 5));
                  } catch (_) {
                    // Hold diagnostics must never change physical acceptance.
                  }
                  stage = 'egress_hold_renewal_ack_wait';
                }
                if (renewal['acknowledged'] == true &&
                    (controller.phase.name == 'connected' ||
                        holdRefreshProbeAttempted)) {
                  holdRenewalAcknowledged = true;
                  break;
                }
                final pause = renewalAckDeadline - holdAckClock.elapsed;
                if (pause > Duration.zero) {
                  await Future<void>.delayed(
                    pause < const Duration(milliseconds: 500)
                        ? pause
                        : const Duration(milliseconds: 500),
                  );
                }
              }
              if (!holdRenewalAcknowledged) {
                stage = renewal['acknowledged'] == true
                    ? 'egress_hold_transfer_unconfirmed'
                    : 'egress_hold_server_ack_missing';
              }
              check(holdRenewalAcknowledged);
              stage = 'egress_capture_hold';
            }
          }
          if (holdObservations.isNotEmpty) {
            try {
              await api
                  .call('POST', '/api/v1/__i028__/egress-hold-observation', {
                    'fixed_probe_attempted': holdProbeAttempted,
                    'fixed_probe_succeeded': holdProbeSucceeded,
                    'observations': holdObservations,
                  })
                  .timeout(const Duration(seconds: 5));
            } catch (_) {
              // Status fallback evidence is diagnostic and cannot break cleanup.
            }
          }
          stage = 'egress_disconnect';
          await closed();
          // Positive control for the synchronized AP audit. The probe UID is
          // temporarily excluded after closing the tunnel, so its fixed DNS
          // and direct-IPv6 source ports should be visible on the bridge.
          // The split rule is restored in finally before the run is released.
          stage = 'egress_excluded_probe';
          await PlatformTunnel.channel.invokeMethod<void>('splitWrite', {
            'packages': ['org.example.vpnpoc'],
          });
          await connected();
          final excluded = await action('probe_all');
          check(
            excluded['ipv4_available'] == true &&
                excluded['egress_is_staging'] == false,
          );
          if (included['ipv4_available'] == true) {
            await fact('same_probe_uid_included_then_excluded_egress');
          }
          // Android rejects split-routing preference changes while the VPN
          // service is up. Disconnect before restoring the default routing.
          stage = 'egress_excluded_disconnect';
          await closed();
          stage = 'egress_split_restore';
          await PlatformTunnel.channel.invokeMethod<void>('splitWrite', {
            'packages': <String>[],
          });
          stage = 'egress_settlement';
          await api.call('POST', '/api/v1/__i028__/verify');
          stage = 'egress_logout';
          await api.call('POST', '/api/v1/auth/logout');
          if (!includedProbePassed) {
            // Retain the failed DNS acceptance result after the full diagnostic
            // window and normal local settlement/cleanup have completed.
            stage = 'included_probe';
            check(false);
          }
          return;
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
          stage = 'sustained_native_state';
          check(snapshot.up && snapshot.network && snapshot.remainingMs > 0);
          if (watch.elapsed.inSeconds >= nextTransfer) {
            stage = 'sustained_transfer_probe';
            final transfer = await action('probe_ipv4');
            if (transfer['egress_is_staging'] != true) {
              final after = await native.snapshot();
              await api.call(
                'POST',
                '/api/v1/__i028__/native_observation_after_failure',
                {
                  'up': after.up,
                  'network': after.network,
                  'handshake_age': after.handshakeAge,
                  'remaining_ms': after.remainingMs,
                },
              );
            }
            check(transfer['egress_is_staging'] == true);
            nextTransfer = watch.elapsed.inSeconds + 20;
          }
        }
        stage = 'sustained_final_probe';
        final sustained = await action('probe_ipv4');
        check(sustained['egress_is_staging'] == true);
        stage = 'sustained_key_stability';
        check(await native.publicKey() == key);
        stage = 'sustained_server_generation';
        await api.call('POST', '/api/v1/__i028__/renewals');
        stage = 'sustained_fact_checkpoint';
        await fact('sustained_real_renewals_over_three_minutes');
        if (controller.trafficRenewalRequested) {
          await fact('advisory_traffic_renewal_requested');
        }
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
        await recordDiagnosticSnapshot('recovery', 'before');
        final recoveryProbe = await action('recovery_probe_ipv4');
        await recordDiagnosticSnapshot('recovery', 'after');
        check(recoveryProbe['egress_is_staging'] == true);
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

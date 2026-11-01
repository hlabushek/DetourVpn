// TEST ENTRYPOINT ONLY: actual Android/native/API, fake Telegram delivery.
// Loopback coordination exists solely in opt-in test_i019_live.py.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('I019 physical two-node staging drill', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: Text('I-019 STAGING TEST ONLY'))),
    );
    await tester.runAsync(() async {
      final api = Api(Uri.parse('http://127.0.0.1:18766'));
      final native = PlatformTunnel();
      final connection = ConnectionController(
        api,
        native,
        pollInterval: const Duration(milliseconds: 500),
        leaseBudgetBytes: 16777216,
      );
      var stage = 'initialize';
      Map<String, Object?> lastProbe = {};
      Future<void> step(String value) async {
        stage = value;
        try {
          final snapshot = await native.snapshot();
          await api.call('POST', '/api/v1/__i019__/progress', {
            'stage': value,
            'phase': connection.phase.name,
            'error': connection.error ?? 'none',
            'has_lease': connection.hasLease,
            'native_up': snapshot.up,
            'native_network': snapshot.network,
            'handshake_age': snapshot.handshakeAge,
            'remaining_ms': snapshot.remainingMs,
          });
        } catch (_) {
          // Authentication/transport may not yet exist; never hide the actual test.
        }
      }

      Future<void> until(
        Future<bool> Function() check, [
        int seconds = 40,
      ]) async {
        final deadline = DateTime.now().add(Duration(seconds: seconds));
        while (DateTime.now().isBefore(deadline)) {
          if (await check()) return;
          await Future<void>.delayed(const Duration(milliseconds: 400));
        }
        throw StateError('i019_stage_$stage');
      }

      Future<void> fact(String name) async {
        await api.call('POST', '/api/v1/__i019__/fact', {'name': name});
      }

      Future<void> operation(String action) async {
        await api.call('POST', '/api/v1/__i019__/$action');
        await until(() async {
          final state = await api.call('POST', '/api/v1/__i019__/job');
          if (state['failed'] == true) throw StateError('i019_stage_$stage');
          return state['done'] == true;
        });
      }

      Future<String?> egress() async {
        try {
          final started = await api.call(
            'POST',
            '/api/v1/__i019__/egress-probe',
            {'stage': stage},
          );
          final probeId = started['probe_id'];
          if (probeId is! String) throw StateError('i019_probe_start_failed');
          // The test API's fixed ADB instrumentation command may take up to
          // 25 seconds; keep polling long enough to receive its sanitized result.
          final deadline = DateTime.now().add(const Duration(seconds: 30));
          Map<String, dynamic>? probe;
          while (DateTime.now().isBefore(deadline)) {
            probe = await api.call(
              'POST',
              '/api/v1/__i019__/egress-probe-result',
              {'probe_id': probeId},
            );
            if (probe['done'] == true) break;
            await Future<void>.delayed(const Duration(milliseconds: 300));
          }
          if (probe == null || probe['done'] != true) {
            lastProbe = {'phase': 'response', 'result': 'timeout'};
            return null;
          }
          final result = probe['result'];
          const allowed = {
            'node_a',
            'node_b',
            'other_address',
            'dns',
            'timeout',
            'route',
            'tls',
            'connect',
            'permission',
            'io',
            'http_error',
            'instrumentation_missing',
            'unexpected_result',
            'adb_error',
            'probe_task_error',
            'unavailable',
          };
          final label = result is String && allowed.contains(result)
              ? result
              : 'unavailable';
          lastProbe = {'phase': 'response', 'result': label};
          return label;
        } catch (_) {
          lastProbe = {'phase': 'response', 'result': 'unavailable'};
          return null;
        }
      }

      Future<void> connectTo(String id, String expectedEgress) async {
        final attempt = stage;
        await step('${attempt}_catalog');
        await connection.refreshNodes();
        connection.selectNode(id);
        expect(connection.selectedNode, id);
        await step('${attempt}_admission');
        await connection.connect();
        if (connection.phase == ConnectionPhase.failed) {
          throw StateError('i019_stage_$stage');
        }
        await step('${attempt}_handshake');
        // WireGuard starts a handshake when traffic is sent. Generate that
        // traffic from the independent probe UID instead of waiting for
        // background phone traffic that may not exist in a later reconnect.
        final trigger = await egress();
        const transientProbeResults = {
          'dns',
          'timeout',
          'route',
          'tls',
          'connect',
          'permission',
          'io',
          'http_error',
          'instrumentation_missing',
          'unexpected_result',
          'adb_error',
          'probe_task_error',
          'unavailable',
        };
        if (trigger != null &&
            trigger != expectedEgress &&
            !transientProbeResults.contains(trigger)) {
          throw StateError('i019_stage_$stage');
        }
        var nextTrigger = DateTime.now().add(const Duration(seconds: 5));
        await until(() async {
          await connection.refresh();
          if (connection.phase == ConnectionPhase.failed) {
            throw StateError('i019_stage_$stage');
          }
          if (connection.phase != ConnectionPhase.connected &&
              DateTime.now().isAfter(nextTrigger)) {
            final retry = await egress();
            if (retry != null &&
                retry != expectedEgress &&
                !transientProbeResults.contains(retry)) {
              throw StateError('i019_stage_$stage');
            }
            nextTrigger = DateTime.now().add(const Duration(seconds: 5));
          }
          return connection.phase == ConnectionPhase.connected;
        });
        expect((await native.snapshot()).confirmed, true);
        await step('${attempt}_egress');
        var consecutive = 0;
        // Fresh independent-UID probes; require two actual egress responses, not UP/handshake.
        for (var probe = 0; probe < 3; probe++) {
          final before = await native.snapshot();
          if (!before.confirmed || before.remainingMs < 15000) break;
          final result = await egress();
          consecutive = result == expectedEgress ? consecutive + 1 : 0;
          if (consecutive == 2) return;
          // A response through another network is a failure, never retried as success.
          if (result != null && result != expectedEgress) break;
          await Future<void>.delayed(const Duration(milliseconds: 500));
        }
        throw StateError('i019_stage_$stage');
      }

      Future<bool> normalNetwork() async {
        if ((await native.snapshot()).up) return false;
        final state = await api.call('POST', '/api/v1/__i019__/normal-network');
        if (state['normal'] != true) return false;
        final address = await egress();
        return address == 'other_address';
      }

      Future<void> disconnectTo() async {
        await connection.disconnect();
        await until(() async {
          await connection.refresh();
          return !connection.hasLease;
        });
        expect((await native.snapshot()).up, false);
        await until(normalNetwork);
      }

      Future<void> recover(Map<String, dynamic> active) async {
        await connection.disconnect();
        expect(connection.remotePending, true);
        final deadline = DateTime.parse(active['deadline'] as String).toUtc();
        while (DateTime.now().toUtc().isBefore(
          deadline.add(const Duration(seconds: 11)),
        )) {
          await Future<void>.delayed(const Duration(milliseconds: 500));
        }
        await until(() async {
          if (connection.canRecover) await connection.recoverExpired();
          return !connection.hasLease;
        });
        // Repeat the actual public recovery endpoint: no second debit.
        final replay = await api.call(
          'POST',
          '/api/v1/tunnels/${active['id']}/recover',
        );
        expect(replay['state'], 'revoked');
      }

      try {
        await step('authentication');
        final challenge = await api.call('POST', '/api/v1/auth/start');
        final issued = await api.call('POST', '/api/v1/__i019__/issue');
        await api.acceptSession(
          await api.call('POST', '/api/v1/auth/verify', {
            'request_id': challenge['request_id'],
            'verifier': challenge['verifier'],
            'code': issued['code'],
          }),
        );
        final ids = await api.call('POST', '/api/v1/__i019__/nodes');
        final baseline = await egress();
        expect(baseline, 'other_address');
        expect(await normalNetwork(), true);
        // Fail early on a foundational B failure before repeating A's expiry windows.
        await step('b_initial_connect');
        await connectTo(ids['b'] as String, 'node_b');
        await fact('b_initial_handshake_egress');
        await step('b_initial_disconnect');
        await disconnectTo();
        await fact('b_initial_disconnect_restores_network');
        if (ids['diagnostic_b_reuse'] == true) {
          // Explicit diagnostic only: no claim about A failure/expiry in this mode.
          await step('b_connect');
          await connectTo(ids['b'] as String, 'node_b');
          await fact('b_handshake_egress');
          await step('b_disconnect');
          await disconnectTo();
          await fact('b_disconnect_restores_network');
          await step('ledger_verify');
          final verified = await api.call('POST', '/api/v1/__i019__/verify');
          expect(verified['ledger_consistent'], true);
          return;
        }
        await step('a_first_connect');
        await connectTo(ids['a'] as String, 'node_a');
        await fact('a_handshake_egress');
        final first = await api.call('POST', '/api/v1/__i019__/active');
        await step('negative_early_recovery');
        try {
          await api.call('POST', '/api/v1/tunnels/${first['id']}/recover');
          throw StateError('i019_stage_$stage');
        } on ClientError catch (failure) {
          expect(failure.code, 'tunnel_recovery_not_ready');
        }
        await fact('early_recovery_denied');
        await step('negative_second_peer');
        final proof = await api.call(
          'POST',
          '/api/v1/tunnels/key-challenge/${first['device_id']}',
        );
        final answer = await native.prove(
          proof['ephemeral_public_key'] as String,
          proof['transcript'] as String,
        );
        try {
          await api.call('POST', '/api/v1/tunnels', {
            'idempotency_key': newId(),
            'device_id': first['device_id'],
            'node_id': ids['b'],
            'proof_id': proof['id'],
            'proof_response': answer,
            'budget_bytes': 1048576,
          });
          throw StateError('i019_stage_$stage');
        } on ClientError catch (failure) {
          expect(failure.code, 'tunnel_exists');
        }
        await fact('second_peer_denied');
        await step('kernel_expiry');
        await api.call('POST', '/api/v1/__i019__/freeze-a');
        final firstDeadline = DateTime.parse(
          first['deadline'] as String,
        ).toUtc();
        while (DateTime.now().toUtc().isBefore(
          firstDeadline.add(const Duration(seconds: 2)),
        )) {
          await Future<void>.delayed(const Duration(milliseconds: 500));
        }
        final kernel = await api.call(
          'POST',
          '/api/v1/__i019__/kernel-expired',
        );
        expect(kernel['expired'], true);
        await fact('kernel_expiry_without_agent_or_api');
        await until(() async => !(await native.snapshot()).up);
        await until(normalNetwork);
        await fact('normal_network_after_expiry');
        await step('first_recovery');
        await recover(first);
        await fact('first_recovery');
        await step('restart_a');
        await operation('return-a');
        await connectTo(ids['a'] as String, 'node_a');
        await fact('a_restart_egress');
        final second = await api.call('POST', '/api/v1/__i019__/active');
        await step('node_failure_cutoff');
        await operation('stop-a');
        expect((await native.snapshot()).up, true);
        final cutoffProbe = await egress();
        expect({'dns', 'timeout', 'route'}.contains(cutoffProbe), true);
        final atCutoff = await native.snapshot();
        expect(atCutoff.up && atCutoff.remainingMs > 0, true);
        expect(
          DateTime.now().toUtc().isBefore(
            DateTime.parse(second['deadline'] as String).toUtc(),
          ),
          true,
        );
        await fact('node_failure_cutoff_before_expiry');
        await step('second_recovery');
        await connection.disconnect();
        expect((await native.snapshot()).up, false);
        await until(normalNetwork);
        await fact('normal_network_after_node_failure');
        await recover(second);
        await fact('second_recovery');
        await step('b_connect');
        await connectTo(ids['b'] as String, 'node_b');
        await fact('b_handshake_egress');
        await step('return_a_while_b_active');
        await operation('return-a');
        expect(await egress(), 'node_b');
        await fact('return_a_does_not_revive_old_peer');
        await step('b_disconnect');
        await disconnectTo();
        await fact('b_disconnect_restores_network');
        await step('ledger_verify');
        final verified = await api.call('POST', '/api/v1/__i019__/verify');
        expect(verified['ledger_consistent'], true);
      } catch (_) {
        try {
          final snapshot = await native.snapshot();
          await api.call('POST', '/api/v1/__i019__/failure', {
            'stage': stage,
            'phase': connection.phase.name,
            'error': connection.error ?? 'none',
            'nodes_error': connection.nodesError ?? 'none',
            'node_reasons': connection.nodes.map((n) => n.reason).toList(),
            'has_lease': connection.hasLease,
            'remote_pending': connection.remotePending,
            'native_up': snapshot.up,
            'native_network': snapshot.network,
            'handshake_age': snapshot.handshakeAge,
            'remaining_ms': snapshot.remainingMs,
            'last_probe': lastProbe,
          });
        } catch (_) {
          // Preserve the original fixed stage even if diagnostic transport is down.
        }
        // Never serialize assertions/remote responses containing session/config data.
        throw StateError('i019_stage_$stage');
      } finally {
        await native.disconnect();
        connection.dispose();
        api.close();
      }
    });
  }, timeout: const Timeout(Duration(minutes: 12)));
}

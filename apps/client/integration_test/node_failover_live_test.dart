// TEST ENTRYPOINT ONLY: actual Android/native/API, fake Telegram delivery.
// Loopback coordination exists solely in opt-in test_i019_live.py.
import 'dart:async';
import 'dart:io';

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
        final client = HttpClient()
          ..connectionTimeout = const Duration(seconds: 3);
        var probePhase = 'request';
        lastProbe = {'phase': probePhase, 'result': 'pending'};
        try {
          final request = await client
              .getUrl(Uri.parse('https://api.ipify.org'))
              .timeout(const Duration(seconds: 4));
          request.followRedirects = false;
          probePhase = 'response';
          final response = await request.close().timeout(
            const Duration(seconds: 4),
          );
          lastProbe = {'phase': probePhase, 'http_status': response.statusCode};
          if (response.statusCode != 200) {
            lastProbe['result'] = 'http_status';
            return null;
          }
          probePhase = 'body';
          final bytes = await response
              .fold<List<int>>([], (a, b) => a..addAll(b))
              .timeout(const Duration(seconds: 4));
          if (bytes.length > 64) {
            lastProbe = {'phase': probePhase, 'result': 'invalid_body'};
            return null;
          }
          final address = String.fromCharCodes(bytes).trim();
          if (InternetAddress.tryParse(address) == null) {
            lastProbe = {'phase': probePhase, 'result': 'invalid_body'};
            return null;
          }
          lastProbe = {
            'phase': probePhase,
            'result': address == '45.10.166.106'
                ? 'node_a'
                : address == '85.209.132.205'
                ? 'node_b'
                : 'other_address',
          };
          return address;
        } on TimeoutException {
          lastProbe = {'phase': probePhase, 'result': 'timeout'};
          return null;
        } on SocketException catch (failure) {
          lastProbe = {
            'phase': probePhase,
            'result': 'socket_error',
            'os_error': failure.osError?.errorCode,
          };
          return null;
        } on HandshakeException {
          lastProbe = {'phase': probePhase, 'result': 'tls_error'};
          return null;
        } catch (_) {
          lastProbe = {'phase': probePhase, 'result': 'other_error'};
          return null;
        } finally {
          client.close(force: true);
        }
      }

      Future<void> connectTo(String id, String host) async {
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
        await until(() async {
          await connection.refresh();
          if (connection.phase == ConnectionPhase.failed) {
            throw StateError('i019_stage_$stage');
          }
          return connection.phase == ConnectionPhase.connected;
        });
        expect((await native.snapshot()).confirmed, true);
        await step('${attempt}_egress');
        var consecutive = 0;
        // Fresh clients each time; require two actual egress responses, not UP/handshake.
        for (var probe = 0; probe < 3; probe++) {
          final before = await native.snapshot();
          if (!before.confirmed || before.remainingMs < 15000) break;
          final result = await egress();
          await api.call('POST', '/api/v1/__i019__/probe', {
            'stage': stage,
            ...lastProbe,
          });
          consecutive = result == host ? consecutive + 1 : 0;
          if (consecutive == 2) return;
          // A response through another network is a failure, never retried as success.
          if (result != null && result != host) break;
          await Future<void>.delayed(const Duration(milliseconds: 500));
        }
        throw StateError('i019_stage_$stage');
      }

      Future<bool> normalNetwork() async {
        if ((await native.snapshot()).up) return false;
        final state = await api.call('POST', '/api/v1/__i019__/normal-network');
        if (state['normal'] != true) return false;
        final address = await egress();
        return address != null &&
            address != '45.10.166.106' &&
            address != '85.209.132.205';
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
        api.acceptSession(
          await api.call('POST', '/api/v1/auth/verify', {
            'request_id': challenge['request_id'],
            'verifier': challenge['verifier'],
            'code': issued['code'],
          }),
        );
        final ids = await api.call('POST', '/api/v1/__i019__/nodes');
        final baseline = await egress();
        expect(baseline != null, true);
        expect(
          baseline != '45.10.166.106' && baseline != '85.209.132.205',
          true,
        );
        expect(await normalNetwork(), true);
        // Fail early on a foundational B failure before repeating A's expiry windows.
        await step('b_initial_connect');
        await connectTo(ids['b'] as String, '85.209.132.205');
        await fact('b_initial_handshake_egress');
        await step('b_initial_disconnect');
        await disconnectTo();
        await fact('b_initial_disconnect_restores_network');
        if (ids['diagnostic_b_reuse'] == true) {
          // Explicit diagnostic only: no claim about A failure/expiry in this mode.
          await step('b_connect');
          await connectTo(ids['b'] as String, '85.209.132.205');
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
        await connectTo(ids['a'] as String, '45.10.166.106');
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
        await connectTo(ids['a'] as String, '45.10.166.106');
        await fact('a_restart_egress');
        final second = await api.call('POST', '/api/v1/__i019__/active');
        await step('node_failure_cutoff');
        await operation('stop-a');
        expect((await native.snapshot()).up, true);
        expect(await egress(), null);
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
        await connectTo(ids['b'] as String, '85.209.132.205');
        await fact('b_handshake_egress');
        await step('return_a_while_b_active');
        await operation('return-a');
        expect(await egress(), '85.209.132.205');
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

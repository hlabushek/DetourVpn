import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/session_store.dart';
import 'package:vpn_client/window_connection.dart';

String _safeFailureMarker(Object error) {
  if (error is ClientError && RegExp(r'^[a-z_]{1,64}$').hasMatch(error.code)) {
    return 'i028_two_device_client_error_${error.code}';
  }
  if (error is StateError) {
    final message = error.message;
    if (RegExp(r'^i028_two_device_[a-z0-9_]{1,96}$').hasMatch(message)) {
      return message;
    }
  }
  if (error is TimeoutException) return 'i028_two_device_timeout';
  if (error is FormatException) return 'i028_two_device_format_error';
  return 'i028_two_device_phase_failed';
}

// TEST ENTRYPOINT ONLY. A and B use separate Android app storage, OTP identities,
// device keys and API sessions. OTP delivery is fake inside the disposable harness.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('I-028 two physical devices', (tester) async {
    await tester.runAsync(() async {
      const phase = String.fromEnvironment('VPN_I028_TWO_PHASE');
      const configuredRole = String.fromEnvironment(
        'VPN_I028_ROLE',
        defaultValue: 'auto',
      );
      if (!{'seed', 'seed_run', 'run', 'cleanup'}.contains(phase) ||
          ({'seed', 'seed_run'}.contains(phase) &&
              !{'a', 'b'}.contains(configuredRole))) {
        throw StateError('i028_two_device_configuration');
      }
      var role = configuredRole;
      const package = String.fromEnvironment(
        'VPN_I028_APP_PACKAGE',
        defaultValue: 'com.detourvpn.client.i028',
      );
      if (package != 'com.detourvpn.client.i028') {
        throw StateError('i028_two_device_package');
      }

      final api = Api(
        Uri.parse('http://127.0.0.1:18766'),
        store: PlatformSessionStore(),
      );
      final native = PlatformTunnel();
      WindowConnectionController? controller;
      var retainSession = false;

      try {
        if (await PlatformSessionStore().i028PackageName() != package) {
          // Never read or clear a session unless this is the isolated I-028 app.
          retainSession = true;
          throw StateError('i028_two_device_package');
        }
        if (phase == 'cleanup') {
          await api.forgetSession();
          return;
        }
        if (phase == 'seed' || phase == 'seed_run') {
          if (await PlatformSessionStore().read() != null) {
            throw StateError('i028_two_device_existing_session');
          }
          final start = await api.call('POST', '/api/v1/auth/start');
          final issued = await api.call(
            'POST',
            '/api/v1/__i028__/two-device/issue/$role',
            {'request_id': start['request_id'], 'verifier': start['verifier']},
          );
          await api.acceptSession(
            await api.call('POST', '/api/v1/auth/verify', {
              'request_id': start['request_id'],
              'verifier': start['verifier'],
              'code': issued['code'],
            }),
          );
          await api.call('POST', '/api/v1/__i028__/two-device/seeded/$role');
          if (await PlatformSessionStore().read() == null) {
            throw StateError('i028_two_device_session_not_saved');
          }
          retainSession = true;
          if (phase == 'seed') return;

          var bothSeeded = false;
          for (var attempt = 0; attempt < 180; attempt++) {
            final status = await api.call(
              'GET',
              '/api/v1/__i028__/two-device/ready/$role',
            );
            if (status['ready'] == true) {
              bothSeeded = true;
              break;
            }
            await Future<void>.delayed(const Duration(seconds: 1));
          }
          if (!bothSeeded) {
            throw StateError('i028_two_device_seed_barrier_timeout');
          }
        } else {
          await api.restoreSession();
          if (!api.authenticated)
            throw StateError('i028_two_device_session_missing');
          retainSession =
              true; // Host cleanup runs the local-only cleanup entrypoint.
        }
        final assigned = await api.call(
          'GET',
          '/api/v1/__i028__/two-device/role',
        );
        role = assigned['role'] as String? ?? 'invalid';
        if (!{'a', 'b'}.contains(role)) {
          throw StateError('i028_two_device_role_unavailable');
        }
        final me = await api.call('GET', '/api/v1/me');
        await api.call('POST', '/api/v1/__i028__/two-device/restored/$role', {
          'user_id': me['id'],
        });
        controller = WindowConnectionController(api, native);
        await controller.refreshNodes();
        final available = controller.nodes.where(
          (node) => node.reason == 'available',
        );
        if (available.length != 1)
          throw StateError('i028_two_device_node_unavailable');
        controller.selectNode(available.single.id);
        await controller.connect();

        var connected = false;
        var nativeReadyReported = false;
        for (var attempt = 0; attempt < 45; attempt++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller.refresh();
          final snapshot = await native.snapshot();
          if (!nativeReadyReported &&
              snapshot.up &&
              snapshot.network &&
              controller.hasLease) {
            await api.call(
              'POST',
              '/api/v1/__i028__/two-device/native-ready/$role',
            );
            nativeReadyReported = true;
          }
          if (snapshot.confirmed && controller.hasLease) {
            connected = true;
            break;
          }
        }
        if (!connected) {
          final snapshot = await native.snapshot();
          final clientError = controller.error;
          if (clientError != null &&
              RegExp(r'^[a-z_]{1,64}$').hasMatch(clientError)) {
            throw StateError('i028_two_device_client_error_$clientError');
          }
          if (!snapshot.up) throw StateError('i028_two_device_native_down');
          if (!controller.hasLease)
            throw StateError('i028_two_device_lease_missing');
          if (!snapshot.network)
            throw StateError('i028_two_device_network_unvalidated');
          if (snapshot.handshakeAge < 0)
            throw StateError('i028_two_device_handshake_missing');
          if (snapshot.handshakeAge > 60)
            throw StateError('i028_two_device_handshake_stale');
          if (snapshot.remainingMs <= 0)
            throw StateError('i028_two_device_lease_expired');
          throw StateError('i028_two_device_connect_unconfirmed');
        }
        final listing = await api.call('GET', WindowConnectionController.root);
        final items = listing['items'];
        if (items is! List || items.length != 1 || items.single is! Map) {
          throw StateError('i028_two_device_connection_list');
        }
        final row = Map<String, dynamic>.from(items.single as Map);
        final grant = row['window'];
        if (grant is! Map) throw StateError('i028_two_device_window_missing');
        await api.call('POST', '/api/v1/__i028__/two-device/connected/$role', {
          'connection_id': row['id'],
          'device_id': row['device_id'],
          'family_id': (await api.call(
            'GET',
            '/api/v1/__i028__/two-device/identity/$role',
          ))['family_id'],
          'window_id': grant['window_id'],
          'slot_id': row['slot_id'],
        });

        var command = 'wait';
        for (var attempt = 0; attempt < 600; attempt++) {
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller.refresh();
          final value = await api.call(
            'GET',
            '/api/v1/__i028__/two-device/command/$role',
          );
          command = value['command'] as String? ?? 'invalid';
          if (command == 'disconnect') break;
          if (command != 'wait')
            throw StateError('i028_two_device_command_invalid');
          if (!(await native.snapshot()).up) {
            throw StateError('i028_two_device_unexpected_native_down');
          }
        }
        if (command != 'disconnect')
          throw StateError('i028_two_device_command_timeout');
        await controller.disconnect();
        var finalNative = await native.snapshot();
        for (var attempt = 0; controller.hasLease && attempt < 30; attempt++) {
          if (finalNative.up)
            throw StateError('i028_two_device_native_disconnect_unconfirmed');
          await Future<void>.delayed(const Duration(seconds: 1));
          await controller.refresh();
          finalNative = await native.snapshot();
        }
        if (finalNative.up)
          throw StateError('i028_two_device_native_disconnect_unconfirmed');
        if (controller.hasLease)
          throw StateError('i028_two_device_remote_revoke_unconfirmed');
        await api.call(
          'POST',
          '/api/v1/__i028__/two-device/disconnected/$role',
        );
      } catch (error) {
        throw StateError(_safeFailureMarker(error));
      } finally {
        try {
          controller?.dispose();
          await native.disconnect();
          if (!retainSession && phase != 'cleanup') await api.forgetSession();
          api.close();
        } catch (_) {
          throw StateError('i028_two_device_local_cleanup_failed');
        }
      }
    });
  }, timeout: const Timeout(Duration(minutes: 12)));
}

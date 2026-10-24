import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/session_store.dart';

// TEST ENTRYPOINT ONLY: real phone/vault/API/PostgreSQL; Telegram is TEST FAKE ONLY.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
    'I-025 physical persistent session with real local authentication',
    (tester) async {
      final origin = Uri.parse('http://127.0.0.1:18766');
      final native = PlatformTunnel();
      final store = PlatformSessionStore();
      var api = Api(origin, store: store);
      var stage = 'start';
      void check(bool value) {
        if (!value) throw StateError('i025_session_stage_$stage');
      }

      Future<void> login() async {
        final start = await api.call('POST', '/api/v1/auth/start');
        final issued = await api.call('POST', '/api/v1/__i025__/issue');
        final tokens = await api.call('POST', '/api/v1/auth/verify', {
          'request_id': start['request_id'],
          'verifier': start['verifier'],
          'code': issued['code'],
        });
        await api.acceptSession(tokens);
        final device = await api.call('POST', '/api/v1/devices', {
          'platform': 'android',
          'key_type': 'x25519',
          'public_key': await native.publicKey(),
        });
        check(device['id'] is String && api.authenticated);
      }

      Future<void> fact(String name) =>
          api.call('POST', '/api/v1/__i025__/fact', {'name': name});
      try {
        await tester.pumpWidget(VpnApp(api: null, native: native));
        check(await store.read() == null);
        stage = 'login';
        await login();
        final saved = await store.read();
        check(saved != null);
        final key = await native.publicKey();
        api.close();
        api = Api(origin, store: store);
        stage = 'restore';
        await api.restoreSession();
        final refreshed = await store.read();
        check(
          api.authenticated &&
              refreshed != null &&
              saved!['refresh_token'] != refreshed['refresh_token'],
        );
        check(await native.publicKey() == key);
        final quota = await api.call('GET', '/api/v1/quota');
        check(quota['available_bytes'] is int);
        await fact('real_flutter_api_keystore_refresh_rotation');
        stage = 'logout';
        await api.call('POST', '/api/v1/auth/logout');
        await api.forgetSession();
        api.close();
        api = Api(origin, store: store);
        await api.restoreSession();
        check(!api.authenticated && await store.read() == null);
        await fact('real_flutter_logout_erases_and_server_revokes');
        stage = 'lost_response_login';
        await login();
        await api.call('POST', '/api/v1/__i025__/drop-next-refresh');
        api.close();
        api = Api(origin, store: store);
        stage = 'lost_response';
        var failedClosed = false;
        try {
          await api.restoreSession();
        } on ClientError catch (error) {
          failedClosed = error.code == 'session_expired';
        }
        check(failedClosed && !api.authenticated && await store.read() == null);
        api.close();
        api = Api(origin, store: store);
        await api.restoreSession();
        check(!api.authenticated);
        await fact('lost_committed_refresh_response_no_durable_replay');
        await api.call('POST', '/api/v1/__i025__/verify');
        check(!(await native.snapshot()).up);
      } catch (_) {
        throw StateError('i025_session_stage_$stage');
      } finally {
        await api.forgetSession();
        api.close();
        await native.disconnect();
      }
    },
  );
}

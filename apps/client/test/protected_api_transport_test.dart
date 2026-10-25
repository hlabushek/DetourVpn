import 'dart:io';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/protected_api_transport.dart';

const origin = 'https://api.example.test';
const token =
    '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';

class RelayProvider implements ProtectedRelayProvider {
  RelayProvider(this.port);
  final int port;
  int calls = 0;

  @override
  Future<ProtectedRelayEndpoint> protectedRelayEndpoint() async {
    calls++;
    return ProtectedRelayEndpoint(port: port, token: token);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
    'opens only an authenticated loopback stream for the pinned origin',
    () async {
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final provider = RelayProvider(server.port);
      final factory = protectedApiSocketFactory(Uri.parse(origin), provider);
      final accepted = server.first;
      final task = await factory(Uri.parse('$origin/api/v1/nodes'), null, null);
      final socket = await task.socket;
      final relaySocket = await accepted;
      final preface = await utf8.decoder
          .bind(relaySocket)
          .transform(const LineSplitter())
          .first
          .timeout(const Duration(seconds: 1));

      expect(preface, 'DETOUR-RELAY/1 $token');
      expect(provider.calls, 1);

      await expectLater(
        factory(
          Uri.parse('https://other.example.test/api/v1/nodes'),
          null,
          null,
        ),
        throwsA(isA<SocketException>()),
      );
      await expectLater(
        factory(Uri.parse('$origin/api/v1/nodes'), 'proxy.invalid', 8080),
        throwsA(isA<SocketException>()),
      );
      expect(provider.calls, 1);

      socket.destroy();
      relaySocket.destroy();
      await server.close();
    },
  );

  test(
    'rejects an untrusted API origin and malformed relay credentials',
    () async {
      expect(
        () => protectedApiSocketFactory(
          Uri.parse('http://api.example.test'),
          RelayProvider(18080),
        ),
        throwsArgumentError,
      );
      expect(ProtectedRelayEndpoint(port: 80, token: token).isValid, isFalse);
      expect(
        ProtectedRelayEndpoint(port: 18080, token: 'short').isValid,
        isFalse,
      );
    },
  );
}

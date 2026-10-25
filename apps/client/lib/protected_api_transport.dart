import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';

final class ProtectedRelayEndpoint {
  const ProtectedRelayEndpoint({required this.port, required this.token});

  final int port;
  final String token;

  bool get isValid =>
      port >= 1024 &&
      port <= 65535 &&
      RegExp(r'^[0-9a-f]{64}$').hasMatch(token);
}

abstract interface class ProtectedRelayProvider {
  Future<ProtectedRelayEndpoint> protectedRelayEndpoint();
}

final class PlatformProtectedRelayProvider implements ProtectedRelayProvider {
  static const _channel = MethodChannel('project.vpn/tunnel.v1');

  @override
  Future<ProtectedRelayEndpoint> protectedRelayEndpoint() async {
    final value = await _channel.invokeMapMethod<String, Object?>(
      'protectedRelayRead',
    );
    final port = value?['port'];
    final token = value?['token'];
    if (port is! int || token is! String) {
      throw const SocketException('Protected relay unavailable');
    }
    final endpoint = ProtectedRelayEndpoint(port: port, token: token);
    if (!endpoint.isValid) {
      throw const SocketException('Protected relay unavailable');
    }
    return endpoint;
  }
}

typedef ProtectedSocketFactory =
    Future<ConnectionTask<Socket>> Function(
      Uri uri,
      String? proxyHost,
      int? proxyPort,
    );

/// Uses an authenticated loopback relay only for the configured API origin.
/// TLS remains in Dart HttpClient, which verifies the API certificate normally.
ProtectedSocketFactory protectedApiSocketFactory(
  Uri apiOrigin,
  ProtectedRelayProvider relayProvider,
) {
  if (apiOrigin.scheme != 'https' ||
      apiOrigin.host.isEmpty ||
      apiOrigin.userInfo.isNotEmpty ||
      apiOrigin.hasQuery ||
      apiOrigin.hasFragment ||
      (apiOrigin.path.isNotEmpty && apiOrigin.path != '/')) {
    throw ArgumentError.value(apiOrigin, 'apiOrigin');
  }

  return (uri, proxyHost, proxyPort) async {
    if (proxyHost != null ||
        proxyPort != null ||
        uri.scheme != 'https' ||
        uri.host != apiOrigin.host ||
        uri.port != apiOrigin.port ||
        uri.userInfo.isNotEmpty) {
      throw const SocketException('Protected API origin rejected');
    }

    final endpoint = await relayProvider.protectedRelayEndpoint();
    if (!endpoint.isValid) {
      throw const SocketException('Protected relay unavailable');
    }

    Socket? socket;
    try {
      socket = await Socket.connect(
        InternetAddress.loopbackIPv4,
        endpoint.port,
        timeout: const Duration(seconds: 5),
      );
      socket.add(ascii.encode('DETOUR-RELAY/1 ${endpoint.token}\r\n'));
      await socket.flush();
      return ConnectionTask.fromSocket<Socket>(
        Future<Socket>.value(socket),
        socket.destroy,
      );
    } catch (_) {
      socket?.destroy();
      rethrow;
    }
  };
}

HttpClient protectedApiHttpClient(
  Uri apiOrigin,
  ProtectedRelayProvider relayProvider,
) {
  final client = HttpClient();
  client.connectionTimeout = const Duration(seconds: 5);
  client.findProxy = (uri) => 'DIRECT';
  client.connectionFactory = protectedApiSocketFactory(
    apiOrigin,
    relayProvider,
  );
  return client;
}

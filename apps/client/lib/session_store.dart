import 'package:flutter/services.dart';

abstract interface class SessionStore {
  Future<Map<String, dynamic>?> read();
  Future<void> write(Map<String, dynamic>? value);
}

/// Android encrypts this origin-bound record with a non-exportable Keystore key.
class PlatformSessionStore implements SessionStore {
  static const _channel = MethodChannel('project.vpn/tunnel.v1');
  @override
  Future<Map<String, dynamic>?> read() async =>
      (await _channel.invokeMapMethod<String, dynamic>('sessionRead'));
  @override
  Future<void> write(Map<String, dynamic>? value) =>
      _channel.invokeMethod<void>('sessionWrite', {'value': value});
}

import 'package:flutter/services.dart';

abstract interface class SessionStore {
  Future<Map<String, dynamic>?> read();
  Future<void> write(Map<String, dynamic>? value);
}

abstract interface class PendingLoginStore {
  Future<Map<String, dynamic>?> readPendingLogin();
  Future<void> writePendingLogin(Map<String, dynamic>? value);
}

/// Android encrypts this origin-bound record with a non-exportable Keystore key.
class PlatformSessionStore implements SessionStore, PendingLoginStore {
  static const _channel = MethodChannel('project.vpn/tunnel.v1');
  Future<String> i028PackageName() async =>
      (await _channel.invokeMethod<String>('i028PackageName')) ?? '';

  @override
  Future<Map<String, dynamic>?> read() async =>
      (await _channel.invokeMapMethod<String, dynamic>('sessionRead'));
  @override
  Future<void> write(Map<String, dynamic>? value) =>
      _channel.invokeMethod<void>('sessionWrite', {'value': value});

  @override
  Future<Map<String, dynamic>?> readPendingLogin() async =>
      (await _channel.invokeMapMethod<String, dynamic>('pendingLoginRead'));

  @override
  Future<void> writePendingLogin(Map<String, dynamic>? value) =>
      _channel.invokeMethod<void>('pendingLoginWrite', {'value': value});
}

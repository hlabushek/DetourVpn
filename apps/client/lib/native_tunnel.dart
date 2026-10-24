import 'package:flutter/services.dart';

class NativeSnapshot {
  const NativeSnapshot({
    required this.up,
    required this.network,
    required this.handshakeAge,
    required this.remainingMs,
    this.reason = '',
  });
  final bool up;
  final bool network;
  final int handshakeAge;
  final int remainingMs;
  final String reason;
  bool get confirmed =>
      up &&
      network &&
      handshakeAge >= 0 &&
      handshakeAge <= 60 &&
      remainingMs > 0;
}

abstract interface class NativeTunnel {
  Future<bool> permission();
  Future<String> publicKey();
  Future<String> prove(String ephemeralKey, String transcript);
  Future<void> connect(String config, int remainingMs);
  Future<void> disconnect();
  Future<NativeSnapshot> snapshot();
  Future<void> openLogin(String url);
}

abstract interface class WindowNative implements NativeTunnel {
  Future<void> connectWindow(
    String config,
    Map<String, dynamic> grant,
    int remainingMs,
  );
  Future<void> advanceWindow(Map<String, dynamic> grant, int remainingMs);
}

class PlatformTunnel implements WindowNative {
  static const channel = MethodChannel('project.vpn/tunnel.v1');
  Map<String, dynamic> _windowArgs(
    Map<String, dynamic> grant,
    int remainingMs,
  ) => {
    'connection': grant['connection_id'],
    'runtime': grant['runtime_id'],
    'generation': grant['generation'],
    'remainingMs': remainingMs,
  };
  @override
  Future<void> connectWindow(
    String config,
    Map<String, dynamic> grant,
    int remainingMs,
  ) => channel.invokeMethod<void>('connectWindow', {
    ..._windowArgs(grant, remainingMs),
    'config': config,
  });
  @override
  Future<void> advanceWindow(Map<String, dynamic> grant, int remainingMs) =>
      channel.invokeMethod<void>(
        'advanceWindow',
        _windowArgs(grant, remainingMs),
      );
  @override
  Future<bool> permission() async =>
      await channel.invokeMethod<bool>('permission') ?? false;
  @override
  Future<String> publicKey() async =>
      (await channel.invokeMethod<String>('publicKey'))!;
  @override
  Future<String> prove(String ephemeralKey, String transcript) async =>
      (await channel.invokeMethod<String>('prove', {
        'ephemeralKey': ephemeralKey,
        'transcript': transcript,
      }))!;
  @override
  Future<void> connect(String config, int remainingMs) =>
      channel.invokeMethod<void>('connect', {
        'config': config,
        'remainingMs': remainingMs,
      });
  @override
  Future<void> disconnect() => channel.invokeMethod<void>('disconnect');
  @override
  Future<NativeSnapshot> snapshot() async {
    final value = (await channel.invokeMapMethod<String, Object?>('snapshot'))!;
    return NativeSnapshot(
      up: value['up'] as bool,
      network: value['network'] as bool,
      handshakeAge: value['handshakeAge'] as int,
      remainingMs: value['remainingMs'] as int,
      reason: value['reason'] as String,
    );
  }

  @override
  Future<void> openLogin(String url) =>
      channel.invokeMethod<void>('openLogin', {'url': url});
}

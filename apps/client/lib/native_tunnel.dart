import 'package:flutter/services.dart';

class NativeSnapshot {
  const NativeSnapshot({
    required this.up,
    required this.network,
    required this.handshakeAge,
    required this.remainingMs,
    this.trafficBytes,
    this.receivedBytes,
    this.reason = '',
  });
  final bool up;
  final bool network;
  final int handshakeAge;
  final int remainingMs;

  /// Approximate native transfer; never a quota meter or settlement evidence.
  final int? trafficBytes;

  /// Approximate inbound tunnel bytes; used only as recent data-path evidence.
  final int? receivedBytes;
  final String reason;
  bool get confirmed =>
      up &&
      network &&
      handshakeAge >= 0 &&
      handshakeAge <= 60 &&
      remainingMs > 0;
}

/// Tracks recent inbound progress without treating outbound-only traffic as
/// confirmation that the tunnel's return path is working.
class InboundTrafficFreshness {
  InboundTrafficFreshness({this.freshFor = const Duration(seconds: 60)});

  final Duration freshFor;
  int? _lastBytes;
  int? _lastProgressAtMs;

  bool observe(int? receivedBytes, int nowMs) {
    if (receivedBytes == null || receivedBytes < 0) return _isFresh(nowMs);
    final previous = _lastBytes;
    if (previous == null || receivedBytes < previous) {
      _lastBytes = receivedBytes;
      _lastProgressAtMs = null;
      return false;
    }
    if (receivedBytes > previous) {
      _lastBytes = receivedBytes;
      _lastProgressAtMs = nowMs;
    }
    return _isFresh(nowMs);
  }

  bool _isFresh(int nowMs) {
    final lastProgressAtMs = _lastProgressAtMs;
    return lastProgressAtMs != null &&
        nowMs >= lastProgressAtMs &&
        nowMs - lastProgressAtMs <= freshFor.inMilliseconds;
  }

  void reset() {
    _lastBytes = null;
    _lastProgressAtMs = null;
  }
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

/// Local non-secret preference needed to select a node after process restart.
abstract interface class NodePreferenceStore {
  Future<String?> readSelectedNode();
  Future<void> writeSelectedNode(String? id);
}

class PlatformTunnel implements WindowNative, NodePreferenceStore {
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
      trafficBytes:
          value['trafficBytes'] is int && (value['trafficBytes'] as int) >= 0
          ? value['trafficBytes'] as int
          : null,
      receivedBytes:
          value['receivedBytes'] is int && (value['receivedBytes'] as int) >= 0
          ? value['receivedBytes'] as int
          : null,
      reason: value['reason'] as String,
    );
  }

  @override
  Future<void> openLogin(String url) =>
      channel.invokeMethod<void>('openLogin', {'url': url});

  @override
  Future<String?> readSelectedNode() =>
      channel.invokeMethod<String>('nodeRead');

  @override
  Future<void> writeSelectedNode(String? id) =>
      id != null &&
          !RegExp(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
          ).hasMatch(id)
      ? Future<void>.error(ArgumentError.value(id, 'id'))
      : channel.invokeMethod<void>('nodeWrite', {'id': id});
}

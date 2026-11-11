import 'package:flutter/services.dart';

/// Capabilities reported by the native engine (Windows only). Android has no such method, and any
/// failure means "none": features are shown only when the engine proves it can enforce them.
Future<Set<String>> readNativeCapabilities() async {
  try {
    final value = await const MethodChannel(
      'project.vpn/tunnel.v1',
    ).invokeListMethod<String>('capabilities');
    return (value ?? const <String>[]).toSet();
  } catch (_) {
    return const <String>{};
  }
}

class NativeSnapshot {
  const NativeSnapshot({
    required this.up,
    required this.network,
    required this.handshakeAge,
    required this.remainingMs,
    this.trafficBytes,
    this.receivedBytes,
    this.reason = '',
    this.nativeRenewal = false,
    this.handshakeSeen = false,
    this.connectionId,
    this.windowGeneration,
    this.restoreActive = false,
    this.networkChangeAgeMs,
  });

  /// ADR-122: milliseconds since the phone's default network last changed; null if never or
  /// unknown (older native builds, Windows).
  final int? networkChangeAgeMs;

  /// ADR-122: the network changed within the last minute, so older evidence proves nothing.
  bool get recentNetworkChange =>
      networkChangeAgeMs != null && networkChangeAgeMs! < 60000;

  /// ADR-121: a headless restore engine (reboot or process-kill restore) owns the tunnel now.
  final bool restoreActive;

  /// ADR-117: the connection and generation the native deadline fence holds right now, so a
  /// restarted UI can adopt a running tunnel. Null when no window is held or on older builds.
  final String? connectionId;
  final int? windowGeneration;

  /// Task 7: the native side saw a handshake since the last connect, even while Dart slept.
  /// Kept after the tunnel stops. Older native builds do not send it (false).
  final bool handshakeSeen;
  final bool up;
  final bool network;
  final int handshakeAge;
  final int remainingMs;

  /// Approximate native transfer; never a quota meter or settlement evidence.
  final int? trafficBytes;

  /// Approximate inbound tunnel bytes; used only as recent data-path evidence.
  final int? receivedBytes;
  final String reason;

  /// ADR-097: the OS-level VPN process renews the window itself; Dart only observes.
  final bool nativeRenewal;
  bool get confirmed =>
      up &&
      network &&
      handshakeAge >= 0 &&
      handshakeAge <= 60 &&
      // ADR-122: a handshake from before the last network change does not prove the new path.
      (networkChangeAgeMs == null ||
          handshakeAge * 1000 < networkChangeAgeMs!) &&
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

/// ADR-097: optional native renewer. Platforms without one keep Dart-driven renewal.
abstract interface class NativeRenewalHost {
  /// True only if the native side accepted and started renewing this exact connection.
  Future<bool> startNativeRenewal(Map<String, Object?> args);
}

/// ADR-121: end the restore engine and keep its running tunnel for the app UI.
abstract interface class RestoreHandover {
  /// True only if the restore engine was ended and the tunnel is still up.
  Future<bool> adoptRestore();
}

abstract interface class WindowNative implements NativeTunnel {
  Future<void> connectWindow(
    String config,
    Map<String, dynamic> grant,
    int remainingMs,
  );
  Future<void> advanceWindow(Map<String, dynamic> grant, int remainingMs);
}

/// ADR-115: a panel server (VLESS + Reality). Only the Windows runner implements the
/// channel method; [profile] holds a credential and must never be logged.
abstract interface class VlessNative {
  Future<void> connectVless(Map<String, Object> profile, int maxMs);
}

/// Local non-secret preference needed to select a node after process restart.
abstract interface class NodePreferenceStore {
  Future<String?> readSelectedNode();
  Future<void> writeSelectedNode(String? id);
}

class PlatformTunnel
    implements
        WindowNative,
        NodePreferenceStore,
        NativeRenewalHost,
        RestoreHandover,
        VlessNative {
  static const channel = MethodChannel('project.vpn/tunnel.v1');
  @override
  Future<bool> adoptRestore() async {
    try {
      return await channel.invokeMethod<bool>('adoptRestore') ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

  @override
  Future<void> connectVless(Map<String, Object> profile, int maxMs) =>
      channel.invokeMethod<void>('connectVless', {...profile, 'maxMs': maxMs});
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
  Future<bool> startNativeRenewal(Map<String, Object?> args) async {
    try {
      return await channel.invokeMethod<bool>('startNativeRenewal', args) ??
          false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

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
      nativeRenewal: value['nativeRenewal'] == true,
      handshakeSeen: value['handshakeSeen'] == true,
      connectionId: value['connection'] is String
          ? value['connection'] as String
          : null,
      windowGeneration:
          value['windowGeneration'] is int &&
              (value['windowGeneration'] as int) >= 1
          ? value['windowGeneration'] as int
          : null,
      restoreActive: value['restoreActive'] == true,
      networkChangeAgeMs:
          value['networkChangeAgeMs'] is int &&
              (value['networkChangeAgeMs'] as int) >= 0
          ? value['networkChangeAgeMs'] as int
          : null,
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

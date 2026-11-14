import 'package:flutter/services.dart';

import 'connected_since.dart';

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

/// The snapshot's `failureStage`: a lowercase stage code, or null for "" / missing / other types.
String? nativeFailureStage(Object? value) {
  if (value is! String) return null;
  return RegExp(r'^[a-z_]{1,64}$').hasMatch(value) ? value : null;
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
    this.desiredConnected = false,
    this.transport,
    this.stopCategory,
    this.alwaysOn,
    this.failureStage,
  });

  /// The native step that failed first in the current window (for example `native_watchdog_restart`,
  /// `native_renewal`). Set only by a native build that names failures; null when none or older.
  /// Used for the diagnostic stage while the tunnel is down.
  final String? failureStage;

  /// ADR-139: the Always-on VPN setting as native last read it (`on`, `off`, `lockdown`,
  /// `unknown`). Null on older builds and on Windows.
  final String? alwaysOn;

  /// ADR-124: the user's persisted wish to stay connected, held natively. Only an explicit
  /// disconnect or a terminal failure clears it. False on older builds and on Windows.
  final bool desiredConnected;

  /// ADR-134: why native is not running the tunnel although the user still wants it (`none`,
  /// `background_start_refused`, ...). Null on older builds.
  final String? stopCategory;

  /// ADR-124: the native supervisor's transport state (`connected`, `degraded`,
  /// `reconnecting`, `waiting_for_network`, ...). Diagnostics; null on older builds.
  final String? transport;

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

/// ADR-133: data-path state from the native byte counters. Mirrors `DataPathWatch.java`; the
/// same thresholds, so the native transport and the Dart status agree.
enum DataPathState { unknown, flowing, quiet, stalled }

/// ADR-133: inbound progress within [flowFor] means flowing. Outbound bytes of at least
/// [stallOutboundBytes] with no inbound progress for [flowFor] is a stall. An idle phone is
/// quiet, never stalled. A stall only changes the displayed confirmation; it never reconnects.
class DataPathHealth {
  DataPathHealth({
    this.flowFor = const Duration(seconds: 120),
    this.stallOutboundBytes = 8192,
  });

  final Duration flowFor;
  final int stallOutboundBytes;
  int? _lastReceived;
  int? _lastOutbound;
  int? _inboundAtMs;
  int? _baseAtMs;
  int _outboundSince = 0;

  void reset() {
    _lastReceived = null;
    _lastOutbound = null;
    _inboundAtMs = null;
    _baseAtMs = null;
    _outboundSince = 0;
  }

  /// [received] is inbound bytes and [transfer] is inbound plus outbound bytes; null or negative
  /// means unknown and changes nothing.
  DataPathState observe(int? received, int? transfer, int nowMs) {
    if (received == null ||
        transfer == null ||
        received < 0 ||
        transfer < 0 ||
        transfer < received) {
      return state(nowMs);
    }
    final outbound = transfer - received;
    final lastReceived = _lastReceived;
    final lastOutbound = _lastOutbound;
    if (lastReceived == null ||
        lastOutbound == null ||
        received < lastReceived ||
        outbound < lastOutbound) {
      // First sample or counter reset: start a fresh baseline.
      _lastReceived = received;
      _lastOutbound = outbound;
      _baseAtMs = nowMs;
      _inboundAtMs = null;
      _outboundSince = 0;
      return state(nowMs);
    }
    if (received > lastReceived) {
      _inboundAtMs = nowMs;
      _outboundSince = 0;
    } else {
      _outboundSince += outbound - lastOutbound;
    }
    _lastReceived = received;
    _lastOutbound = outbound;
    return state(nowMs);
  }

  DataPathState state(int nowMs) {
    final base = _baseAtMs;
    if (base == null) return DataPathState.unknown;
    final inbound = _inboundAtMs;
    if (inbound != null && nowMs - inbound <= flowFor.inMilliseconds) {
      return DataPathState.flowing;
    }
    final since = inbound ?? base;
    if (nowMs - since >= flowFor.inMilliseconds &&
        _outboundSince >= stallOutboundBytes) {
      return DataPathState.stalled;
    }
    return DataPathState.quiet;
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

/// ADR-124: an explicit user disconnect ends the native "stay connected" intent. A plain
/// [NativeTunnel.disconnect] only stops the engine (cleanups and server switches use it too).
abstract interface class UserIntentHost {
  Future<void> endUserIntent();
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
        UserIntentHost,
        VlessNative,
        ConnectedSinceStore {
  static const channel = MethodChannel('project.vpn/tunnel.v1');

  @override
  Future<void> endUserIntent() async {
    try {
      await channel.invokeMethod<void>('setIntent', {'connected': false});
    } on PlatformException {
      // The disconnect that follows still stops the engine.
    } on MissingPluginException {
      // Windows and older builds have no native intent.
    }
  }

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
      desiredConnected: value['desiredConnected'] == true,
      stopCategory: value['stopCategory'] is String
          ? value['stopCategory'] as String
          : null,
      alwaysOn:
          const {'on', 'off', 'lockdown', 'unknown'}.contains(value['alwaysOn'])
          ? value['alwaysOn'] as String
          : null,
      failureStage: nativeFailureStage(value['failureStage']),
      transport: value['transport'] is String
          ? value['transport'] as String
          : null,
    );
  }

  @override
  Future<void> openLogin(String url) =>
      channel.invokeMethod<void>('openLogin', {'url': url});

  @override
  Future<ConnectedSince?> readConnectedSince() async {
    try {
      final value = await channel.invokeMapMethod<String, Object?>(
        'connectedSinceRead',
      );
      final id = value?['id'];
      final since = value?['sinceMs'];
      return id is String && since is int && since > 0
          ? ConnectedSince(id, since)
          : null;
    } on MissingPluginException {
      return null; // Windows and older builds keep the timer in memory only.
    }
  }

  @override
  Future<void> writeConnectedSince(ConnectedSince? value) async {
    try {
      await channel.invokeMethod<void>(
        'connectedSinceWrite',
        value == null
            ? {'id': null}
            : {'id': value.id, 'sinceMs': value.sinceMs},
      );
    } on MissingPluginException {
      // Windows and older builds keep the timer in memory only.
    }
  }

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

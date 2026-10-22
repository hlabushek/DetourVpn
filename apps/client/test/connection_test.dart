import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';

// TEST-ONLY fakes. These tests do not prove VPN/egress or Android behaviour.
class FakeNative implements NativeTunnel {
  bool allowed = true;
  bool up = false;
  bool network = true;
  int age = -1;
  int starts = 0;
  int stops = 0;
  Completer<bool>? pendingPermission;
  bool failStop = false;
  String reason = '';
  Completer<void>? snapshotGate;
  @override
  Future<bool> permission() async =>
      pendingPermission == null ? allowed : pendingPermission!.future;
  @override
  Future<String> publicKey() async => 'test-only-public-key';
  @override
  Future<String> prove(String ephemeralKey, String transcript) async =>
      'test-only-proof';
  @override
  Future<void> connect(String config, int remainingMs) async {
    starts++;
    up = true;
  }

  @override
  Future<void> disconnect() async {
    stops++;
    if (failStop) throw StateError('test-only-disconnect-failure');
    up = false;
    if (pendingPermission != null && !pendingPermission!.isCompleted)
      pendingPermission!.complete(false);
  }

  @override
  Future<NativeSnapshot> snapshot() async {
    if (snapshotGate != null) await snapshotGate!.future;
    return NativeSnapshot(
      up: up,
      network: network,
      handshakeAge: age,
      remainingMs: 59000,
      reason: reason,
    );
  }

  @override
  Future<void> openLogin(String url) async {}
}

class FakeApi implements TunnelApi {
  String remote = 'active';
  bool lostCreate = false;
  bool failNetwork = false;
  bool deletePending = false;
  bool quotaFails = false;
  String? rejectCreate;
  Completer<void>? createGate;
  final List<String> creates = [];
  int deletes = 0;
  bool revoking = false;
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (failNetwork) throw const ClientError('network_unavailable');
    if (path == '/api/v1/quota') {
      if (quotaFails) throw const ClientError('network_unavailable');
      return {
        'available_bytes': 9223372036854775807,
        'granted_bytes': 9223372036854775807,
        'used_bytes': 0,
        'reserved_bytes': 0,
        'revision': 0,
      };
    }
    if (path.startsWith('/api/v1/quota/history?'))
      return {
        'items': <Map<String, dynamic>>[],
        'snapshot_revision': 0,
        'next_before_sequence': null,
      };
    if (path == '/api/v1/devices') return {'id': 'test-device'};
    if (path.contains('/key-challenge/'))
      return {
        'id': 'test-proof',
        'ephemeral_public_key': 'test-only-key',
        'transcript': 'test-only-transcript',
      };
    if (path == '/api/v1/tunnels' && method == 'POST') {
      creates.add(body!['idempotency_key'] as String);
      if (rejectCreate != null) throw ClientError(rejectCreate!);
      if (createGate != null) await createGate!.future;
      if (lostCreate) {
        lostCreate = false;
        throw const ClientError('network_unavailable');
      }
      return {'id': 'test-tunnel', 'state': remote};
    }
    if (path.endsWith('/config'))
      return {
        'tunnel_id': 'test-tunnel',
        'expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(seconds: 60))
            .toIso8601String(),
        'config': 'test-only-config',
      };
    if (method == 'DELETE') {
      deletes++;
      revoking = deletePending;
      return {'state': deletePending ? 'revoke_pending' : 'revoked'};
    }
    if (revoking)
      return {'state': deletePending ? 'revoke_pending' : 'revoked'};
    return {'id': 'test-tunnel', 'state': remote};
  }
}

void main() {
  late FakeApi api;
  late FakeNative native;
  late ConnectionController c;
  setUp(() {
    api = FakeApi();
    native = FakeNative();
    c = ConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
    );
  });
  tearDown(() => c.dispose());
  test('permission denial never creates peer or starts native VPN', () async {
    native.allowed = false;
    await c.connect();
    expect(c.error, 'permission_denied');
    expect(api.creates, isEmpty);
    expect(native.starts, 0);
  });
  test('a background status refresh cannot silently swallow Connect', () async {
    native.snapshotGate = Completer<void>();
    final refresh = c.refresh();
    await c.connect();
    expect(api.creates.length, 1);
    expect(native.starts, 1);
    native.snapshotGate!.complete();
    await refresh;
    expect(native.up, true);
  });
  test(
    'UP without handshake never says connected; confirmed only after real status input',
    () async {
      await c.connect();
      await c.refresh();
      expect(c.phase, ConnectionPhase.reconnecting);
      native.age = 1;
      await c.refresh();
      expect(c.phase, ConnectionPhase.connected);
      native.network = false;
      await c.refresh();
      expect(c.phase, ConnectionPhase.reconnecting);
      native.network = true;
      native.age = 181;
      await c.refresh();
      expect(c.phase, ConnectionPhase.reconnecting);
    },
  );
  test('system VPN removal stops local and revokes remote', () async {
    await c.connect();
    native.up = false;
    await c.refresh();
    expect(c.phase, ConnectionPhase.failed);
    expect(c.error, 'native_down');
    expect(api.deletes, 1);
  });
  test(
    'native lease expiry is handled even while API is unavailable',
    () async {
      await c.connect();
      native.reason = 'lease_expired';
      native.up = false;
      api.failNetwork = true;
      await c.refresh();
      expect(c.phase, ConnectionPhase.failed);
      expect(native.up, false);
      expect(c.remotePending, true);
    },
  );
  test('server revoke never leaves native connected', () async {
    await c.connect();
    api.remote = 'revoke_pending';
    await c.refresh();
    expect(native.up, false);
    expect(c.error, 'remote_revoked');
  });
  test('API transport loss does not silently destroy a live lease', () async {
    await c.connect();
    api.failNetwork = true;
    await c.refresh();
    expect(c.phase, ConnectionPhase.reconnecting);
    expect(native.up, true);
    api.failNetwork = false;
    native.age = 1;
    await c.refresh();
    expect(c.phase, ConnectionPhase.connected);
  });
  test('quota error does not create native VPN', () async {
    api.rejectCreate = 'insufficient_quota';
    await c.connect();
    expect(c.error, 'insufficient_quota');
    expect(native.starts, 0);
    expect(c.hasLease, false);
  });
  test(
    'lost create response reconciles with the SAME idempotency key then revokes',
    () async {
      api.lostCreate = true;
      await c.connect();
      expect(api.creates.length, 2);
      expect(api.creates.toSet().length, 1);
      expect(api.deletes, 1);
      expect(native.starts, 0);
      expect(c.hasLease, false);
    },
  );
  test(
    'double connect and cancellation during POST cannot activate late peer',
    () async {
      api.createGate = Completer<void>();
      final first = c.connect();
      while (api.creates.isEmpty) {
        await Future<void>.delayed(Duration.zero);
      }
      await c.connect();
      final stop = c.disconnect();
      await Future<void>.delayed(Duration.zero);
      expect(
        native.stops,
        greaterThan(0),
      ); // Native stops before POST completes.
      api.createGate!.complete();
      await first;
      await stop;
      expect(api.creates.length, 1);
      expect(native.starts, 0);
      expect(api.deletes, 1);
    },
  );
  test('cancel pending Android permission does not create a lease', () async {
    native.pendingPermission = Completer<bool>();
    final first = c.connect();
    await c.disconnect();
    await first;
    expect(api.creates, isEmpty);
    expect(native.starts, 0);
    expect(c.phase, ConnectionPhase.disconnected);
  });
  test(
    'remote revoke_pending is visible and blocks another create until ACK',
    () async {
      await c.connect();
      api.deletePending = true;
      await c.disconnect();
      expect(c.remotePending, true);
      expect(c.hasLease, true);
      expect(native.up, false);
      await c.connect();
      expect(api.creates.length, 1);
      api.deletePending = false;
      await c.refresh();
      expect(c.hasLease, false);
    },
  );
  test(
    'native disconnect failure cannot be presented as disconnected',
    () async {
      await c.connect();
      native.failStop = true;
      await c.disconnect();
      expect(c.phase, ConnectionPhase.failed);
      expect(c.error, 'native_disconnect_failed');
      expect(native.up, true);
    },
  );
  test(
    'integer bytes retained; unavailable API marks cached quota stale',
    () async {
      await c.refreshQuota();
      expect(c.availableBytes, 9223372036854775807);
      expect(c.quotaStale, false);
      final updated = c.quotaUpdatedAt;
      api.quotaFails = true;
      await c.refreshQuota();
      expect(c.availableBytes, 9223372036854775807);
      expect(c.quotaUpdatedAt, updated);
      expect(c.quotaStale, true);
    },
  );
}

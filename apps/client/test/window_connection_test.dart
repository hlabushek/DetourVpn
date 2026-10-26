import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/window_connection.dart';
import 'connection_test.dart' show FakeNative;

// TEST ONLY simulated API/native. No Android, node, DNS or egress evidence.
const node = '11111111-1111-4111-8111-111111111111';
const device = '22222222-2222-4222-8222-222222222222';
const connection = '33333333-3333-4333-8333-333333333333';
const runtime = '44444444-4444-4444-8444-444444444444';

class Native extends FakeNative implements WindowNative {
  int advances = 0;
  int remaining = 59000;
  bool failPublicKey = false;
  @override
  Future<String> publicKey() async {
    if (failPublicKey) throw StateError('TEST ONLY native key failure');
    return super.publicKey();
  }

  @override
  Future<NativeSnapshot> snapshot() async => NativeSnapshot(
    up: up,
    network: network,
    handshakeAge: age,
    remainingMs: remaining,
    reason: reason,
  );
  @override
  Future<void> connectWindow(
    String config,
    Map<String, dynamic> grant,
    int remainingMs,
  ) async {
    await connect(config, remainingMs);
    // Real monotonic controller clock must advance before a later ACK can extend
    // its deadline. An instantaneous fake platform reply can share the same ms.
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }

  @override
  Future<void> advanceWindow(
    Map<String, dynamic> grant,
    int remainingMs,
  ) async {
    advances++;
  }
}

class WindowsApi implements TunnelApi {
  int generation = 1;
  String window = '55555555-5555-4555-8555-555555555555';
  String state = 'active';
  bool lostRenew = false;
  bool lostCreate = false;
  bool pending = false;
  bool tamper = false;
  bool denied = false;
  bool oldConnection = false;
  bool failDeletes = false;
  bool deviceBound = false;
  String? invalidResponsePath;
  int creates = 0;
  int deletes = 0;
  final budgets = <int>[];
  final renewKeys = <String>[];
  final createKeys = <String>[];
  final calls = <String>[];
  Completer<void>? renewalGate;
  late DateTime issued = DateTime.now().toUtc();
  Map<String, dynamic> view() => {
    'id': connection,
    'node_id': node,
    'device_id': device,
    'slot_id': 1,
    'state': state,
    'meter': 'kernel-outer-ip.v1',
    'client_config': state == 'active' ? 'TEST ONLY PROFILE' : null,
    'window': {
      'contract_version': 3,
      'connection_id': connection,
      'window_id': window,
      'node_id': node,
      'runtime_id': tamper ? node : runtime,
      'slot_id': 1,
      'generation': generation,
      'public_key': 'test-only-public-key',
      'issued_at': issued.toIso8601String(),
      'not_after': issued.add(const Duration(seconds: 90)).toIso8601String(),
      'budget_bytes': 16777216,
    },
  };
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    calls.add('$method $path');
    if (path == invalidResponsePath) {
      throw const ClientError('invalid_response');
    }
    if (path.endsWith('/quota') || path.contains('/quota/history')) return {};
    if (path.endsWith('/devices')) {
      deviceBound = true;
      return {'id': device};
    }
    if (path.contains('/key-challenge/'))
      return {
        'id': device,
        'ephemeral_public_key': 'TEST ONLY',
        'transcript': 'TEST ONLY',
      };
    if (method == 'GET' && path == WindowConnectionController.root) {
      if (!deviceBound) throw const ClientError('session_device_mismatch');
      return {
        'items': oldConnection ? [view()] : <Map<String, dynamic>>[],
      };
    }
    if (method == 'DELETE') {
      deletes++;
      if (failDeletes) throw const ClientError('network_unavailable');
      state = 'closed';
      return view();
    }
    if (path.endsWith('/renew')) {
      final request = body!;
      budgets.add(request['budget_bytes'] as int);
      renewKeys.add(request['idempotency_key'] as String);
      if (renewalGate != null) await renewalGate!.future;
      if (denied) throw const ClientError('insufficient_quota');
      if (generation == 1) {
        generation++;
        window = request['idempotency_key'] as String;
        issued = issued.add(const Duration(seconds: 1));
      }
      if (lostRenew) {
        lostRenew = false;
        throw const ClientError('network_unavailable');
      }
      state = pending ? 'pending' : 'active';
      return view();
    }
    if (method == 'POST' && path == WindowConnectionController.root) {
      creates++;
      final request = body!;
      budgets.add(request['budget_bytes'] as int);
      createKeys.add(request['idempotency_key'] as String);
      if (lostCreate) {
        lostCreate = false;
        throw const ClientError('network_unavailable');
      }
      return view();
    }
    return view();
  }
}

void main() {
  late WindowsApi api;
  late Native native;
  late WindowConnectionController c;
  setUp(() {
    api = WindowsApi();
    native = Native()..age = 0;
    c = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
    );
    c.selectedNode = node;
  });
  tearDown(() => c.dispose());
  test('production window budget remains 1 MiB by default', () {
    expect(WindowConnectionController.budgetBytes, 1048576);
  });
  test(
    'invalid registration response identifies its bounded diagnostic stage',
    () async {
      api.invalidResponsePath = '/api/v1/devices';
      await c.connect();
      expect(c.error, 'invalid_response');
      expect(c.diagnosticStage, 'device_registration');
      expect(native.starts, 0);
    },
  );
  test(
    'native public-key failure is not reported as invalid API response',
    () async {
      native.failPublicKey = true;
      await c.refresh();
      expect(c.error, 'native_unavailable');
      expect(c.diagnosticStage, 'device_key_read');
      expect(api.calls.where((call) => call.contains('/devices')), isEmpty);
      expect(
        api.calls.where((call) => call.contains('/window-tunnels/')),
        isEmpty,
      );
    },
  );
  test('active ACK starts native; confirmed handshake drives status', () async {
    await c.connect();
    expect(api.budgets, [WindowConnectionController.budgetBytes]);
    expect(native.starts, 1);
    await c.refresh();
    expect(api.budgets, [
      WindowConnectionController.budgetBytes,
      WindowConnectionController.budgetBytes,
    ]);
    expect(native.advances, 1);
    expect(c.phase, ConnectionPhase.connected);
  });
  test(
    'fresh OTP session binds phone key before reconciling connections',
    () async {
      await c.refresh();
      expect(c.error, isNull);
      expect(c.phase, ConnectionPhase.disconnected);
      expect(
        api.calls.indexOf('POST /api/v1/devices'),
        lessThan(api.calls.indexOf('GET ${WindowConnectionController.root}')),
      );
    },
  );
  test(
    'new OTP family rebinds the existing phone key before reconciliation',
    () async {
      await c.refresh();
      api.deviceBound = false;
      c.onAuthenticatedSessionChanged();
      await c.refresh();
      expect(
        api.calls.where((call) => call == 'POST /api/v1/devices').length,
        2,
      );
      expect(
        api.calls
            .where((call) => call == 'GET ${WindowConnectionController.root}')
            .length,
        2,
      );
      expect(c.error, isNull);
    },
  );
  test(
    'lost renewal response retries same intent and extends only once',
    () async {
      await c.connect();
      api.lostRenew = true;
      await c.refresh();
      expect(native.advances, 0);
      await c.refresh();
      expect(api.renewKeys.toSet().length, 1);
      expect(native.advances, 1);
    },
  );
  test('pending renewal never extends native deadline', () async {
    await c.connect();
    api.pending = true;
    await c.refresh();
    expect(native.advances, 0);
    expect(native.up, true);
    api.pending = false;
    await c.refresh();
    expect(native.advances, 1);
  });
  test('disconnect racing renewal cannot revive VPN', () async {
    await c.connect();
    api.renewalGate = Completer<void>();
    final refresh = c.refresh();
    while (api.renewKeys.isEmpty) {
      await Future<void>.delayed(Duration.zero);
    }
    final stop = c.disconnect();
    api.renewalGate!.complete();
    await Future.wait([refresh, stop]);
    expect(native.up, false);
    expect(native.advances, 0);
    expect(c.hasLease, false);
  });
  test('wrong runtime closes VPN and revokes remote intent', () async {
    await c.connect();
    api.tamper = true;
    await c.refresh();
    expect(native.up, false);
    expect(api.deletes, 1);
  });
  test('quota rejection stops VPN instead of guessing new access', () async {
    await c.connect();
    api.denied = true;
    await c.refresh();
    expect(native.up, false);
    expect(api.deletes, 1);
  });
  test(
    'confirmed server close clears an earlier revoke-unconfirmed warning',
    () async {
      await c.connect();
      api.failDeletes = true;
      await c.disconnect();
      expect(c.remotePending, true);
      expect(c.error, 'revoke_unconfirmed');
      api.failDeletes = false;
      await c.refresh();
      expect(c.hasLease, false);
      expect(c.remotePending, false);
      expect(c.error, isNull);
    },
  );
  test(
    'ambiguous create is reconciled with original idempotency key',
    () async {
      api.lostCreate = true;
      await c.connect();
      expect(api.creates, 2);
      expect(api.createKeys.toSet().length, 1);
      expect(native.starts, 0);
      expect(api.deletes, 1);
    },
  );
  test(
    'restart reconciliation closes old device connection before admission',
    () async {
      api.oldConnection = true;
      await c.refresh();
      expect(api.deletes, 1);
      expect(api.creates, 0);
      expect(native.starts, 0);
    },
  );
  test('permission denial never creates a remote connection', () async {
    native.allowed = false;
    await c.connect();
    expect(api.creates, 0);
    expect(c.error, 'permission_denied');
  });
  test('expired native deadline prevents any new renewal', () async {
    await c.connect();
    native.remaining = 0;
    await c.refresh();
    expect(api.renewKeys, isEmpty);
    expect(native.up, false);
    expect(api.deletes, 1);
  });
  test('native down cannot be resurrected by fresh API metadata', () async {
    await c.connect();
    native.up = false;
    await c.refresh();
    expect(native.advances, 0);
    expect(api.renewKeys, isEmpty);
    expect(api.deletes, 1);
  });
}

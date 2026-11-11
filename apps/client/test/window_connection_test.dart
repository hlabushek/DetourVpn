import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show MissingPluginException;
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/platform_info.dart';
import 'package:vpn_client/settings_screen.dart';
import 'package:vpn_client/split_apps.dart';
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
  int? traffic;
  int? received;
  bool seen = false;

  /// ADR-122: milliseconds since the phone's network changed (null = never).
  int? networkChange;
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
    trafficBytes: traffic,
    receivedBytes: received,
    reason: reason,
    handshakeSeen: seen,
    networkChangeAgeMs: networkChange,
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

/// TEST ONLY native renewer host: records the hand-off, never renews anything itself.
class RenewingNative extends Native implements NativeRenewalHost {
  final handoffs = <Map<String, Object?>>[];
  bool renewing = false;

  /// ADR-117: the window the native fence holds (what ClientApplication.snapshot reports).
  String? heldConnection;
  int? heldGeneration;
  @override
  Future<bool> startNativeRenewal(Map<String, Object?> args) async {
    handoffs.add(args);
    renewing = true;
    return true;
  }

  @override
  Future<NativeSnapshot> snapshot() async => NativeSnapshot(
    up: up,
    network: network,
    handshakeAge: age,
    remainingMs: remaining,
    trafficBytes: traffic,
    receivedBytes: received,
    reason: reason,
    nativeRenewal: renewing && up,
    connectionId: up ? heldConnection : null,
    windowGeneration: up ? heldGeneration : null,
  );
}

/// TEST ONLY: a tunnel restored by the headless restore engine (ADR-121), which the UI can take
/// over. It renews from Dart there, so native renewal is off until the UI starts it.
class HandoverNative extends RenewingNative implements RestoreHandover {
  bool restoreActive = true;
  bool handoverWorks = true;
  int handovers = 0;
  @override
  Future<bool> adoptRestore() async {
    handovers++;
    if (!handoverWorks) return false;
    restoreActive = false;
    return up;
  }

  @override
  Future<NativeSnapshot> snapshot() async => NativeSnapshot(
    up: up,
    network: network,
    handshakeAge: age,
    remainingMs: remaining,
    reason: reason,
    nativeRenewal: renewing && up,
    connectionId: up ? heldConnection : null,
    windowGeneration: up ? heldGeneration : null,
    restoreActive: restoreActive,
  );
}

/// TEST ONLY: an engine whose channel has no native renewal (the headless restore engine before
/// ADR-117 answered notImplemented, which Flutter raises as MissingPluginException).
class NoRenewalChannelNative extends Native implements NativeRenewalHost {
  @override
  Future<bool> startNativeRenewal(Map<String, Object?> args) =>
      throw MissingPluginException('TEST ONLY no handler');
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
  String meter = 'kernel-outer-ip.v1';
  int slot = 1;
  bool deviceBound = false;
  bool failRenewToken = false;

  /// ADR-119 TEST ONLY knobs: DELETE answers revoke_pending this many times before closing;
  /// with [freshCreates] every create starts a new active (or, for [pendingNodes], pending)
  /// generation-1 connection on the requested node.
  int pendingDeletes = 0;
  bool freshCreates = false;
  Set<String> pendingNodes = {};
  String nodeId = node;
  final deletedNodes = <String>[];
  final devicePlatforms = <Object?>[];
  String? invalidResponsePath;
  int creates = 0;
  int grantBudget = 1048576;
  int deletes = 0;
  final budgets = <int>[];
  final renewKeys = <String>[];
  final createKeys = <String>[];
  final calls = <String>[];
  Completer<void>? renewalGate;
  late DateTime issued = DateTime.now().toUtc();
  Map<String, dynamic> view() => {
    'id': connection,
    'node_id': nodeId,
    'device_id': device,
    'slot_id': slot,
    'state': state,
    'meter': meter,
    'client_config': state == 'active' ? 'TEST ONLY PROFILE' : null,
    'window': {
      'contract_version': 3,
      'connection_id': connection,
      'window_id': window,
      'node_id': nodeId,
      'runtime_id': tamper ? node : runtime,
      'slot_id': slot,
      'generation': generation,
      'public_key': 'test-only-public-key',
      'issued_at': issued.toIso8601String(),
      'not_after': issued.add(const Duration(seconds: 90)).toIso8601String(),
      'budget_bytes': grantBudget,
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
      devicePlatforms.add(body?['platform']);
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
      deletedNodes.add(nodeId);
      if (failDeletes) throw const ClientError('network_unavailable');
      if (pendingDeletes > 0) {
        pendingDeletes--;
        state = 'revoke_pending';
        return view();
      }
      state = 'closed';
      return view();
    }
    if (path.endsWith('/renew-token')) {
      if (failRenewToken) throw const ClientError('not_found');
      return {'renew_token': 'TEST-ONLY-renew-token-not-a-real-secret'};
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
      if (freshCreates) {
        nodeId = request['node_id'] as String;
        state = pendingNodes.contains(nodeId) ? 'pending' : 'active';
        generation = 1;
        issued = DateTime.now().toUtc();
      }
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
  // The orbit backdrop animates forever, so pumpAndSettle would time out.
  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 4; i++) {
      await tester.pump(const Duration(milliseconds: 300));
    }
  }

  Widget settingsHost() => MaterialApp(
    home: SettingsScreen(onTheme: () {}, controller: c),
  );
  testWidgets(
    'apps-outside-VPN row opens the screen directly while VPN is off',
    (tester) async {
      await tester.pumpWidget(settingsHost());
      expect(
        find.text('Исключённые приложения используют обычную сеть'),
        findsOneWidget,
      );
      await tester.tap(find.text('Приложения вне VPN'));
      await settle(tester);
      expect(find.byType(SplitAppsScreen), findsOneWidget);
      expect(find.text('Отключить VPN?'), findsNothing);
    },
  );
  testWidgets(
    'apps-outside-VPN row stays tappable while connected and offers to disconnect',
    (tester) async {
      await tester.runAsync(() => c.connect());
      await tester.pumpWidget(settingsHost());
      expect(c.hasLease, isTrue);
      expect(
        find.text('VPN включён: для изменения его нужно отключить'),
        findsOneWidget,
      );
      await tester.tap(find.text('Приложения вне VPN'));
      await settle(tester);
      expect(find.text('Отключить VPN?'), findsOneWidget);
      // Cancel keeps the tunnel up and does not open the screen.
      await tester.tap(find.text('Отмена'));
      await settle(tester);
      expect(native.up, isTrue);
      expect(find.byType(SplitAppsScreen), findsNothing);
      // Confirm disconnects first, then opens the screen.
      await tester.tap(find.text('Приложения вне VPN'));
      await settle(tester);
      await tester.tap(find.text('Отключить и открыть'));
      await settle(tester);
      expect(native.up, isFalse);
      expect(c.hasLease, isFalse);
      expect(find.byType(SplitAppsScreen), findsOneWidget);
    },
  );
  group('ADR-097 native renewal', () {
    late RenewingNative renewing;
    WindowConnectionController controller({int seconds = 900}) {
      c.dispose();
      renewing = RenewingNative()..age = 0;
      return c = WindowConnectionController(
        api,
        renewing,
        pollInterval: const Duration(days: 1),
        windowSeconds: seconds,
        renewalOrigin: Uri.parse('https://api.example.invalid'),
      )..selectedNode = node;
    }

    test(
      'long windows hand a scoped token to native after connecting',
      () async {
        controller();
        await c.connect();
        expect(
          api.calls,
          contains(
            'POST ${WindowConnectionController.root}/$connection/renew-token',
          ),
        );
        final args = renewing.handoffs.single;
        expect(args['token'], 'TEST-ONLY-renew-token-not-a-real-secret');
        expect(args['origin'], 'https://api.example.invalid');
        expect(args['connection'], connection);
        expect(args['runtime'], runtime);
        expect(args['windowSeconds'], 900);
        expect(args['generation'], 1);
      },
    );

    test(
      'pool node grants (inner-IP meter, slot above 16) are accepted',
      () async {
        controller(seconds: 90);
        api.meter = 'kernel-inner-ip.v1';
        api.slot = 200;
        await c.connect();
        expect(c.hasLease, isTrue);
        expect(c.error, isNull);
      },
    );

    test('an unknown meter is still refused', () async {
      controller(seconds: 90);
      api.meter = 'kernel-unknown.v1';
      await c.connect();
      expect(c.hasLease, isFalse);
    });

    test('90 s windows and an old API keep Dart renewal', () async {
      controller(seconds: 90);
      await c.connect();
      expect(renewing.handoffs, isEmpty);
      controller();
      api.failRenewToken = true;
      api.state = 'active';
      await c.connect();
      expect(renewing.handoffs, isEmpty);
      expect(c.hasLease, isTrue);
    });

    test('while native renews, Dart only adopts its windows', () async {
      controller();
      await c.connect();
      // Native renewed twice while the app slept: generation 3 with native's own window ids.
      api.generation = 3;
      api.window = '66666666-6666-4666-8666-666666666666';
      renewing.remaining = 500000;
      await c.refresh();
      expect(api.renewKeys, isEmpty);
      expect(renewing.advances, 0);
      expect(c.error, isNull);
      expect(c.hasLease, isTrue);
      // A pending window during a native renewal is waited out, not treated as a failure.
      api.state = 'pending';
      await c.refresh();
      expect(c.error, isNull);
      expect(renewing.up, isTrue);
      api.state = 'active';
      await c.refresh();
      expect(c.error, isNull);
      expect(api.renewKeys, isEmpty);
    });
  });
  group('ADR-117 running tunnel after a UI restart', () {
    late RenewingNative renewing;
    WindowConnectionController reopened() {
      c.dispose();
      renewing = RenewingNative()
        ..age = 0
        ..up = true
        ..renewing = true
        ..heldConnection = connection
        ..heldGeneration = 1
        ..remaining = 500000;
      api.oldConnection = true;
      // A new UI engine: fresh controller, nothing selected yet.
      return c = WindowConnectionController(
        api,
        renewing,
        pollInterval: const Duration(days: 1),
        windowSeconds: 900,
        renewalOrigin: Uri.parse('https://api.example.invalid'),
      );
    }

    test('a reopened UI adopts the natively renewed tunnel', () async {
      reopened();
      await c.refresh();
      expect(renewing.stops, 0);
      expect(api.deletes, 0);
      expect(api.creates, 0);
      expect(c.hasLease, isTrue);
      expect(c.selectedNode, node);
      expect(c.error, isNull);
      expect(c.phase, ConnectionPhase.connected);
      // Later refreshes keep mirroring native, with no Dart renewal.
      await c.refresh();
      expect(c.error, isNull);
      expect(api.renewKeys, isEmpty);
    });

    test(
      'a connect tapped right after reopening keeps the running tunnel',
      () async {
        reopened()..selectedNode = node;
        await c.connect();
        expect(renewing.stops, 0);
        expect(api.creates, 0);
        expect(c.hasLease, isTrue);
      },
    );

    test('a native window that does not match the server is stopped', () async {
      reopened();
      renewing.heldConnection = '99999999-9999-4999-8999-999999999999';
      await c.refresh();
      expect(renewing.stops, greaterThanOrEqualTo(1));
      expect(api.deletes, 1);
      expect(c.hasLease, isFalse);
    });

    test('a server generation older than native is not adopted', () async {
      reopened();
      renewing.heldGeneration = 2;
      await c.refresh();
      expect(renewing.stops, greaterThanOrEqualTo(1));
      expect(c.hasLease, isFalse);
    });

    test(
      'without native renewal the old fail-closed reconcile stays',
      () async {
        reopened();
        renewing.renewing = false;
        await c.refresh();
        expect(renewing.stops, greaterThanOrEqualTo(1));
        expect(api.deletes, 1);
        expect(c.hasLease, isFalse);
      },
    );

    test(
      'an engine without native renewal still connects (restore path)',
      () async {
        c.dispose();
        final plain = NoRenewalChannelNative()..age = 0;
        c = WindowConnectionController(
          api,
          plain,
          pollInterval: const Duration(days: 1),
          windowSeconds: 900,
          renewalOrigin: Uri.parse('https://api.example.invalid'),
        )..selectedNode = node;
        await c.connect();
        expect(c.error, isNull);
        expect(c.hasLease, isTrue);
        expect(plain.up, isTrue);
      },
    );
  });

  group('ADR-121 hand-over of a restored tunnel to the UI', () {
    late HandoverNative restored;
    WindowConnectionController reopened() {
      c.dispose();
      restored = HandoverNative()
        ..age = 0
        ..up = true
        ..heldConnection = connection
        ..heldGeneration = 1
        ..remaining = 500000;
      api.oldConnection = true;
      return c = WindowConnectionController(
        api,
        restored,
        pollInterval: const Duration(days: 1),
        windowSeconds: 900,
        renewalOrigin: Uri.parse('https://api.example.invalid'),
      );
    }

    test(
      'the UI takes over the restored tunnel and starts native renewal',
      () async {
        reopened();
        await c.refresh();
        expect(restored.handovers, 1);
        expect(restored.stops, 0);
        expect(api.deletes, 0);
        expect(api.creates, 0);
        expect(restored.handoffs, hasLength(1));
        expect(restored.handoffs.single['connection'], connection);
        expect(c.hasLease, isTrue);
        expect(c.selectedNode, node);
        expect(c.error, isNull);
      },
    );

    test(
      'a renewal the restore engine finished during hand-over is applied',
      () async {
        reopened();
        // Native still holds generation 1 with little time left; the server already issued 2.
        restored.remaining = 30000;
        api.generation = 2;
        api.window = '66666666-6666-4666-8666-666666666666';
        await c.refresh();
        expect(restored.advances, 1);
        expect(restored.stops, 0);
        expect(c.hasLease, isTrue);
      },
    );

    test('a server two generations ahead is not taken over', () async {
      reopened();
      api.generation = 3;
      api.window = '66666666-6666-4666-8666-666666666666';
      await c.refresh();
      expect(restored.handovers, 0);
      expect(restored.stops, greaterThanOrEqualTo(1));
      expect(c.hasLease, isFalse);
    });

    test('a failed hand-over falls back to the fail-closed stop', () async {
      reopened();
      restored.handoverWorks = false;
      await c.refresh();
      expect(restored.handovers, 1);
      expect(restored.stops, greaterThanOrEqualTo(1));
      expect(api.deletes, 1);
      expect(c.hasLease, isFalse);
    });

    test(
      'a native window that does not match the server is never taken over',
      () async {
        reopened();
        restored.heldConnection = '99999999-9999-4999-8999-999999999999';
        await c.refresh();
        expect(restored.handovers, 0);
        expect(restored.stops, greaterThanOrEqualTo(1));
        expect(c.hasLease, isFalse);
      },
    );
  });

  group('ADR-122 network switch', () {
    test('a handshake from before the network change is not proof', () {
      NativeSnapshot at(int handshakeAge, int? change) => NativeSnapshot(
        up: true,
        network: true,
        handshakeAge: handshakeAge,
        remainingMs: 59000,
        networkChangeAgeMs: change,
      );
      expect(at(10, null).confirmed, isTrue);
      expect(
        at(10, 5000).confirmed,
        isFalse,
      ); // handshake 10 s ago, switch 5 s ago
      expect(at(2, 5000).confirmed, isTrue); // new handshake after the switch
      expect(at(10, 20000).confirmed, isTrue);
      expect(at(10, 5000).recentNetworkChange, isTrue);
      expect(at(10, 60000).recentNetworkChange, isFalse);
    });

    test(
      'after a switch the status says restoring until a new handshake',
      () async {
        await c.connect();
        await c.refresh();
        expect(c.phase, ConnectionPhase.connected);
        // Wi-Fi -> mobile: the last handshake (30 s) happened on the old network.
        native.age = 30;
        native.received = 1000;
        native.networkChange = 5000;
        await c.refresh();
        expect(c.phase, ConnectionPhase.reconnecting);
        expect(c.detail, 'Сеть сменилась, восстанавливается связь');
        expect(c.error, isNull);
        // Inbound bytes alone do not prove the new path within the first minute.
        native.received = 5000;
        await c.refresh();
        expect(c.phase, ConnectionPhase.reconnecting);
        // A handshake on the new network confirms it again.
        native.age = 2;
        native.networkChange = 9000;
        await c.refresh();
        expect(c.phase, ConnectionPhase.connected);
        expect(c.detail, 'Свежий handshake подтверждён');
      },
    );
  });

  group('ADR-119 connect, reconnect and server switch', () {
    const other = '77777777-7777-4777-8777-777777777777';

    test(
      'reconnect starts once a still-closing connection has closed',
      () async {
        final auto = WindowConnectionController(
          api,
          native,
          pollInterval: const Duration(days: 1),
          renewBeforeMs: 1000,
          autoReconnectDelay: const Duration(milliseconds: 20),
        )..selectedNode = node;
        addTearDown(auto.dispose);
        await auto.connect();
        api.state = 'revoke_pending';
        api.pendingDeletes = 1;
        await auto.refresh();
        expect(auto.error, 'window_not_active');
        expect(auto.hasLease, isTrue);
        await Future<void>.delayed(const Duration(milliseconds: 150));
        expect(api.creates, 1); // nothing can start while the old one closes
        api.freshCreates = true;
        await auto.refresh(); // the revoke completes here
        expect(auto.hasLease, isFalse);
        await Future<void>.delayed(const Duration(milliseconds: 300));
        expect(api.creates, greaterThanOrEqualTo(2));
        expect(auto.hasLease, isTrue);
        await auto.disconnect();
      },
    );

    test('a user disconnect cancels a reconnect waiting for cleanup', () async {
      final auto = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 1000,
        autoReconnectDelay: const Duration(milliseconds: 20),
      )..selectedNode = node;
      addTearDown(auto.dispose);
      await auto.connect();
      api.state = 'revoke_pending';
      api.pendingDeletes = 1;
      await auto.refresh();
      await auto.disconnect();
      await auto.refresh();
      await Future<void>.delayed(const Duration(milliseconds: 300));
      expect(api.creates, 1);
    });

    test(
      'connect waits for the previous connection to finish closing',
      () async {
        final w = WindowConnectionController(
          api,
          native,
          pollInterval: const Duration(days: 1),
          renewBeforeMs: 90000,
          revokeWaitInterval: const Duration(milliseconds: 5),
        )..selectedNode = node;
        addTearDown(w.dispose);
        await w.connect();
        api.pendingDeletes = 3;
        await w.disconnect();
        expect(w.hasLease, isTrue);
        api.freshCreates = true;
        await w.connect();
        expect(w.error, isNull);
        expect(w.hasLease, isTrue);
        expect(api.creates, 2);
      },
    );

    test(
      'a revoke that never closes is reported, with no second connection',
      () async {
        final w = WindowConnectionController(
          api,
          native,
          pollInterval: const Duration(days: 1),
          renewBeforeMs: 90000,
          revokeWaitInterval: const Duration(milliseconds: 1),
          revokeWaitChecks: 3,
        )..selectedNode = node;
        addTearDown(w.dispose);
        await w.connect();
        api.pendingDeletes = 100;
        await w.disconnect();
        await w.connect();
        expect(w.error, 'revoke_unconfirmed');
        expect(api.creates, 1);
      },
    );

    test('switching servers while connected moves to the new server', () async {
      final w = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 90000,
        revokeWaitInterval: const Duration(milliseconds: 5),
      );
      addTearDown(w.dispose);
      w.nodes = [
        NodeOption(node, 'A', 'r', 'available'),
        NodeOption(other, 'B', 'r', 'available'),
      ];
      api.freshCreates = true;
      w.selectedNode = node;
      await w.connect();
      // The old selection guard ignores a choice while connected.
      w.selectNode(other);
      expect(w.selectedNode, node);
      await w.switchNode(other);
      expect(w.selectedNode, other);
      expect(w.error, isNull);
      expect(w.hasLease, isTrue);
      expect(api.creates, 2);
      expect(api.deletedNodes, [node]);
    });

    test(
      'switching servers during a pending connect leaves no stale connection',
      () async {
        final w = WindowConnectionController(
          api,
          native,
          pollInterval: const Duration(milliseconds: 20),
          renewBeforeMs: 90000,
          revokeWaitInterval: const Duration(milliseconds: 5),
          autoReconnectDelay: null,
        );
        addTearDown(w.dispose);
        w.nodes = [
          NodeOption(node, 'A', 'r', 'available'),
          NodeOption(other, 'B', 'r', 'available'),
        ];
        api.freshCreates = true;
        api.pendingNodes = {node};
        w.selectedNode = node;
        final first = w.connect();
        await Future<void>.delayed(const Duration(milliseconds: 60));
        expect(w.busy, isTrue);
        expect(w.hasLease, isTrue); // pending connection on A
        await w.switchNode(other);
        await first;
        w.pausePolling();
        expect(w.selectedNode, other);
        expect(w.error, isNull);
        expect(w.hasLease, isTrue);
        expect(api.creates, 2);
        // A was revoked before B was created; nothing else was left behind.
        expect(api.deletedNodes, [node]);
        expect(native.up, isTrue);
      },
    );

    test('a second switch while one is running is ignored', () async {
      final w = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 90000,
        revokeWaitInterval: const Duration(milliseconds: 5),
      );
      addTearDown(w.dispose);
      w.nodes = [
        NodeOption(node, 'A', 'r', 'available'),
        NodeOption(other, 'B', 'r', 'available'),
      ];
      api.freshCreates = true;
      w.selectedNode = node;
      await w.connect();
      final one = w.switchNode(other);
      final two = w.switchNode(node);
      await Future.wait([one, two]);
      expect(w.selectedNode, other);
      expect(api.creates, 2);
    });

    test(
      'a new connection the server did not activate has its own code and bounded retries',
      () async {
        final w = WindowConnectionController(
          api,
          native,
          pollInterval: const Duration(days: 1),
          renewBeforeMs: 90000,
          autoReconnectDelay: const Duration(milliseconds: 5),
        )..selectedNode = node;
        addTearDown(w.dispose);
        api.state = 'revoke_pending';
        await w.connect();
        expect(w.error, 'window_activation_refused');
        expect(w.diagnosticStage, 'lease_validation');
        await Future<void>.delayed(const Duration(milliseconds: 1000));
        expect(
          api.creates,
          1 + WindowConnectionController.maxActivationRetries,
        );
        await w.disconnect();
      },
    );

    test('each stop cause has its own accurate Russian text', () {
      expect(messageFor('window_not_active'), contains('подключается заново'));
      expect(messageFor('window_activation_refused'), contains('не включил'));
      expect(
        messageFor('window_generation_conflict'),
        isNot(contains('подключается заново')),
      );
    });
  });
  test('production window budget remains 1 MiB by default', () {
    expect(WindowConnectionController.budgetBytes, 1048576);
  });
  test(
    'native traffic can request early renewal without changing byte budget',
    () async {
      c.dispose();
      c = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 0,
      )..selectedNode = node;
      native.traffic = 0;
      await c.connect();
      native.traffic = 524287;
      await c.refresh();
      expect(api.renewKeys, isEmpty);
      expect(c.trafficRenewalRequested, false);
      native.traffic = 524288;
      await c.refresh();
      expect(api.renewKeys.length, 1);
      expect(c.trafficRenewalRequested, true);
      expect(api.budgets, [1048576, 1048576]);
      expect(native.advances, 1);
      await c.refresh();
      expect(api.renewKeys.length, 1);
    },
  );
  test(
    'advisory counter reset and absence do not request a guessed renewal',
    () async {
      c.dispose();
      c = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 0,
      )..selectedNode = node;
      native.traffic = 0;
      await c.connect();
      native.traffic = 400000;
      await c.refresh();
      native.traffic = null;
      await c.refresh();
      native.traffic = 2;
      await c.refresh();
      expect(api.renewKeys, isEmpty);
    },
  );
  test(
    'early traffic renewal reuses its idempotency key after response loss',
    () async {
      c.dispose();
      c = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 0,
      )..selectedNode = node;
      native.traffic = 0;
      await c.connect();
      api.lostRenew = true;
      native.traffic = 524288;
      await c.refresh();
      native.traffic = 700000;
      await c.refresh();
      expect(api.renewKeys.length, 2);
      expect(api.renewKeys.toSet().length, 1);
      expect(native.advances, 1);
    },
  );
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
  test('device registration sends the Android platform by default', () async {
    await c.connect();
    expect(api.devicePlatforms, ['android']);
  });
  test('Windows client registers its device as windows', () async {
    debugIsWindowsOverride = true;
    addTearDown(() => debugIsWindowsOverride = false);
    await c.connect();
    expect(api.devicePlatforms, ['windows']);
  });
  testWidgets('settings hide Android-only rows on Windows', (tester) async {
    debugIsWindowsOverride = true;
    addTearDown(() => debugIsWindowsOverride = false);
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(settingsHost());
    await settle(tester);
    expect(find.text('Приложения вне VPN'), findsOneWidget);
    expect(find.text('Работа в фоне'), findsNothing);
    expect(find.text('Системные настройки VPN'), findsNothing);
    expect(find.textContaining('Always-on не поддерживается'), findsOneWidget);
  });
  testWidgets('settings keep the Android rows on Android', (tester) async {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(settingsHost());
    await settle(tester);
    expect(find.text('Работа в фоне'), findsOneWidget);
    expect(find.text('Системные настройки VPN'), findsOneWidget);
  });
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
    expect(c.trafficRenewalRequested, false);
  });
  test(
    'recent inbound tunnel progress confirms an established session',
    () async {
      await c.connect();
      await c.refresh(); // Establish the fresh-handshake barrier first.
      expect(c.phase, ConnectionPhase.connected);
      native.age = 181;
      native.received = 100;
      await c.refresh();
      expect(c.phase, ConnectionPhase.reconnecting);
      native.received = 101;
      await c.refresh();
      expect(c.phase, ConnectionPhase.connected);
      expect(c.detail, 'Входящий трафик через VPN подтверждён');
    },
  );
  test(
    'overdue first handshake asks the user to reconnect, advisory only',
    () async {
      final slow = WindowConnectionController(
        api,
        native,
        pollInterval: const Duration(days: 1),
        renewBeforeMs: 90000,
        handshakeHintAfter: Duration.zero,
      )..selectedNode = node;
      native.age = -1;
      await slow.connect();
      await slow.refresh();
      expect(slow.phase, ConnectionPhase.reconnecting);
      expect(slow.detail, contains('Нажмите «Отключить»'));
      expect(native.starts, 1); // No automatic reconnect.
      slow.dispose();
    },
  );
  test('outbound-only traffic does not confirm a stale handshake', () async {
    await c.connect();
    await c.refresh(); // Establish the fresh-handshake barrier first.
    native.age = 181;
    native.traffic = 100;
    await c.refresh();
    native.traffic = 200;
    await c.refresh();
    expect(c.phase, ConnectionPhase.reconnecting);
  });
  test(
    'pending renewal retains connected status on recent inbound progress',
    () async {
      await c.connect();
      await c.refresh();
      expect(c.phase, ConnectionPhase.connected);
      final advancesBeforePending = native.advances;
      api.pending = true;
      native.age = 181;
      native.received = 100;
      await c.refresh();
      expect(c.phase, ConnectionPhase.reconnecting);
      native.received = 101;
      await c.refresh();
      expect(c.phase, ConnectionPhase.connected);
      expect(c.detail, 'Входящий трафик через VPN подтверждён');
      expect(
        native.advances,
        advancesBeforePending,
      ); // Pending never extends the native lease.
    },
  );
  test('pending renewal cannot establish before a fresh handshake', () async {
    await c.connect();
    native.age = 181;
    native.received = 100;
    native.traffic = 0;
    await c.refresh();
    expect(c.phase, ConnectionPhase.reconnecting);
    final advancesBeforePending = native.advances;
    api.pending = true;
    native.received = 101;
    native.traffic = 600000;
    await c.refresh();
    expect(c.renewalPending, true);
    expect(c.phase, ConnectionPhase.reconnecting);
    expect(native.advances, advancesBeforePending);
  });
  test('inbound activity expires and a counter reset clears confirmation', () {
    final activity = InboundTrafficFreshness();
    expect(activity.observe(100, 1000), false);
    expect(activity.observe(101, 2000), true);
    expect(activity.observe(101, 62000), true);
    expect(activity.observe(101, 62001), false);
    expect(activity.observe(0, 63000), false);
    expect(activity.observe(1, 64000), true);
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
  test('a window that expired in the background reconnects by itself', () async {
    final auto = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: const Duration(milliseconds: 20),
    )..selectedNode = node;
    addTearDown(auto.dispose);
    await auto.connect();
    native.remaining = 0;
    await auto.refresh();
    expect(auto.error, 'lease_expired');
    // Task 7: the fake never reported a handshake, and the export says so.
    expect(auto.diagnosticHandshake, 'not_seen');
    await Future<void>.delayed(const Duration(milliseconds: 300));
    // The fake API issues one window, so the second attempt fails later; what matters is that
    // the client tried again on its own.
    expect(api.creates, greaterThanOrEqualTo(2));
    await auto.disconnect();
  });
  test('a window the server stopped treating as active reconnects', () async {
    final auto = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 1000,
      autoReconnectDelay: const Duration(milliseconds: 20),
    )..selectedNode = node;
    addTearDown(auto.dispose);
    await auto.connect();
    api.state = 'revoke_pending';
    await auto.refresh();
    expect(auto.error, 'window_not_active');
    await Future<void>.delayed(const Duration(milliseconds: 300));
    expect(api.creates, greaterThanOrEqualTo(2));
    await auto.disconnect();
  });
  test(
    'an expiry after a confirmed handshake is exported as confirmed',
    () async {
      await c.connect();
      native.age = 1;
      await c.refresh();
      native.remaining = 0;
      await c.refresh();
      expect(c.error, 'lease_expired');
      expect(c.diagnosticHandshake, 'confirmed');
    },
  );
  test(
    'a handshake only the native side saw (app asleep) is exported as confirmed',
    () async {
      await c.connect();
      // Dart never saw a fresh handshake age; the VPN process remembers one.
      native.seen = true;
      native.remaining = 0;
      await c.refresh();
      expect(c.error, 'lease_expired');
      expect(c.diagnosticHandshake, 'confirmed');
    },
  );
  test('the native memory alone never changes the connection status', () async {
    await c.connect();
    await c.refresh();
    final before = (c.phase, c.detail, c.error);
    native.seen = true;
    await c.refresh();
    expect((c.phase, c.detail, c.error), before);
    expect(c.diagnosticHandshake, isNull);
  });
  test('user disconnect is never undone by auto reconnect', () async {
    final auto = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: const Duration(milliseconds: 20),
    )..selectedNode = node;
    addTearDown(auto.dispose);
    await auto.connect();
    native.remaining = 0;
    await auto.refresh();
    await auto.disconnect();
    await Future<void>.delayed(const Duration(milliseconds: 300));
    expect(native.up, false);
  });
  test('auto reconnect has no hard cap and survives failing attempts', () async {
    // Every create fails transiently: the old limit stopped after 4 tries in 10 minutes.
    final failing = _FailingCreates(api);
    final retrying = WindowConnectionController(
      failing,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: const Duration(milliseconds: 5),
    )..selectedNode = node;
    addTearDown(retrying.dispose);
    await retrying.connect();
    native.remaining = 0;
    await retrying.refresh();
    expect(retrying.error, 'lease_expired');
    failing.fail = true;
    // Backoff 5, 12.5, 37.5, 75 ms ...: at least five attempts within ~1 s.
    await Future<void>.delayed(const Duration(milliseconds: 1200));
    expect(failing.failedCreates, greaterThanOrEqualTo(5));
    expect(retrying.phase, ConnectionPhase.failed);
    await retrying.disconnect();
    final after = failing.failedCreates;
    await Future<void>.delayed(const Duration(milliseconds: 400));
    expect(
      failing.failedCreates,
      after,
    ); // a user disconnect cancels the retries
  });
  test('lost VPN consent is reported and never reconnected', () async {
    final auto = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: const Duration(milliseconds: 5),
    )..selectedNode = node;
    addTearDown(auto.dispose);
    await auto.connect();
    final creates = api.creates;
    native.up = false;
    native.reason = 'vpn_revoked';
    await auto.refresh();
    expect(auto.error, 'vpn_revoked');
    await Future<void>.delayed(const Duration(milliseconds: 200));
    expect(api.creates, creates);
  });
  test('a tunnel the phone dropped early reconnects by itself', () async {
    final auto = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: const Duration(milliseconds: 5),
    )..selectedNode = node;
    addTearDown(auto.dispose);
    await auto.connect();
    final creates = api.creates;
    native.up = false;
    native.reason = 'native_restart_failed';
    await auto.refresh();
    expect(auto.error, 'native_down');
    await Future<void>.delayed(const Duration(milliseconds: 200));
    expect(api.creates, greaterThan(creates));
    await auto.disconnect();
  });
  test('native down cannot be resurrected by fresh API metadata', () async {
    await c.connect();
    native.up = false;
    await c.refresh();
    // The phone stopped the VPN while the window still had time: not an expired window.
    expect(c.error, 'native_down');
    expect(native.advances, 0);
    expect(api.renewKeys, isEmpty);
    expect(api.deletes, 1);
  });
}

/// TEST ONLY: delegates to [WindowsApi] but can fail every connection create transiently.
class _FailingCreates implements TunnelApi {
  _FailingCreates(this.inner);
  final WindowsApi inner;
  bool fail = false;
  int failedCreates = 0;
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) {
    if (fail && method == 'POST' && path == WindowConnectionController.root) {
      failedCreates++;
      throw const ClientError('window_node_unavailable');
    }
    return inner.call(method, path, body);
  }
}

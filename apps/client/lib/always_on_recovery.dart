import 'dart:async';

import 'package:flutter/foundation.dart' show visibleForTesting;
import 'package:flutter/services.dart';

import 'api.dart';
import 'connection.dart';
import 'native_tunnel.dart';
import 'protected_api_transport.dart';
import 'session_store.dart';
import 'window_connection.dart';

const _recoveryChannel = MethodChannel('project.vpn/tunnel.v1');

/// Runs only after Android starts the VPN service in Always-on mode.
/// A stored node ID is a preference; a fresh API session and lease are required.
Future<void> runAlwaysOnRecovery() async {
  final store = PlatformSessionStore();
  final native = PlatformTunnel();
  await _runAlwaysOnRecovery(
    store: store,
    native: native,
    readSelectedNode: native.readSelectedNode,
    createApi: (origin, sessionStore) => Api.protected(
      origin,
      relayProvider: PlatformProtectedRelayProvider(),
      store: sessionStore,
    ),
  );
}

/// Injects local fakes for the startup state machine; production uses the
/// platform-backed entrypoint above and never receives these dependencies.
@visibleForTesting
Future<void> runAlwaysOnRecoveryWithDependencies({
  required SessionStore store,
  required WindowNative native,
  required Future<String?> Function() readSelectedNode,
  required Api Function(Uri origin, SessionStore store) createApi,
}) => _runAlwaysOnRecovery(
  store: store,
  native: native,
  readSelectedNode: readSelectedNode,
  createApi: createApi,
);

Future<void> _runAlwaysOnRecovery({
  required SessionStore store,
  required WindowNative native,
  required Future<String?> Function() readSelectedNode,
  required Api Function(Uri origin, SessionStore store) createApi,
}) async {
  Api? api;
  WindowConnectionController? controller;
  try {
    final saved = await store.read();
    final rawOrigin = saved?['origin'];
    if (rawOrigin is! String) throw const ClientError('session_missing');
    final origin = Uri.tryParse(rawOrigin);
    if (origin == null) throw const ClientError('invalid_origin');

    api = createApi(origin, store);
    await api.restoreSession();
    if (!api.authenticated) throw const ClientError('session_expired');

    controller = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(seconds: 3),
    );
    await controller.refreshNodes();
    if (controller.nodesError != null) {
      throw const ClientError('node_validation_failed');
    }
    final selected = await readSelectedNode();
    if (selected == null) throw const ClientError('window_node_required');
    controller.selectNode(selected);
    if (controller.selectedNode != selected) {
      throw const ClientError('window_node_unavailable');
    }

    // connect() reconciles/revokes prior server state and creates a fresh v3 lease.
    await controller.connect();
    if (controller.phase == ConnectionPhase.failed || !controller.hasLease) {
      throw const ClientError('recovery_failed');
    }
    await _recoveryChannel.invokeMethod<void>('bootstrapComplete');

    // Keep the controller alive to renew the lease and enforce its monotonic deadline.
    await Completer<void>().future;
  } catch (_) {
    controller?.dispose();
    try {
      await native.disconnect();
    } catch (_) {
      // The native deadline and Android Lockdown remain the final fail-closed boundary.
    }
    try {
      await _recoveryChannel.invokeMethod<void>('bootstrapFailed');
    } catch (_) {
      // If the channel is unavailable, the Android service remains non-sticky.
    }
    api?.close();
  }
}

import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'country_flag.dart';
import 'connection.dart';
import 'native_tunnel.dart';
import 'panel_servers.dart';
import 'platform_info.dart';

/// Explicit v3 selection, never falls back to the bounded v2 protocol.
class WindowConnectionController extends ConnectionController {
  WindowConnectionController(
    super.api,
    WindowNative super.native, {
    super.pollInterval,
    this.renewBeforeMs = 55000,
    this.handshakeHintAfter = const Duration(seconds: 25),
    this.autoReconnectDelay = const Duration(seconds: 2),
    this.windowSeconds = defaultWindowSeconds,
    this.revokeWaitInterval = const Duration(seconds: 1),
    this.revokeWaitChecks = 10,
    Uri? renewalOrigin,
    bool? panelVless,
  }) : _renewalOrigin = renewalOrigin,
       panelVless = panelVless ?? isWindowsClient;

  /// ADR-115: this engine can run panel servers (VLESS + Reality). Windows only today.
  final bool panelVless;
  bool _panelUp = false;
  @visibleForTesting
  bool get panelConnected => _panelUp;
  final Duration? autoReconnectDelay;

  /// ADR-119: before a new connection, how often and how many times to check that the previous
  /// one finished closing (the node confirms a revoke within a few seconds).
  final Duration revokeWaitInterval;
  final int revokeWaitChecks;

  /// ADR-119: a refresh failure qualified for automatic reconnect while the old connection was
  /// still closing; reconnect once that cleanup finishes.
  bool _reconnectAfterCleanup = false;

  /// ADR-119: connect-time activation refusals retried in a row (bounded; quota is reserved).
  static const maxActivationRetries = 3;
  int _activationRetries = 0;
  bool _switching = false;
  final List<int> _autoReconnectAt = [];
  static const root = '/api/v1/window-tunnels';
  static const budgetBytes = int.fromEnvironment(
    'VPN_WINDOW_BUDGET_BYTES',
    defaultValue: 1048576,
  );

  /// Requested window length. 90 keeps the legacy request body (no field sent),
  /// so a build only asks for long windows once the API and nodes accept them.
  static const defaultWindowSeconds = int.fromEnvironment(
    'VPN_WINDOW_SECONDS',
    defaultValue: 90,
  );
  final int windowSeconds;
  final Uri? _renewalOrigin;
  static const maxWindowMs = 900000;

  /// Single-peer nodes meter the outer IP and use slots 1..16; pool nodes (ADR-101) meter the
  /// inner IP and use slots 1..250. Both come from the same API contract (`v3_contracts.py`).
  static const _meters = {'kernel-outer-ip.v1', 'kernel-inner-ip.v1'};
  static const maxSlotId = 250;
  final int renewBeforeMs;

  /// Long windows renew once a third has elapsed (10 of 15 min left), leaving
  /// several retry chances before expiry; 90s windows keep [renewBeforeMs].
  int get _renewBefore => max(renewBeforeMs, windowSeconds * 1000 - 300000);
  Map<String, int> get _windowRequest =>
      windowSeconds == 90 ? const {} : {'window_seconds': windowSeconds};

  /// After this long without any confirmed handshake, the status asks the user to reconnect.
  /// Advisory text only: no automatic reconnect, because every new window reserves quota.
  final Duration handshakeHintAfter;
  int _connectedAtMs = 0;
  final _clock = Stopwatch()..start();
  Map<String, dynamic>? _createIntent;
  Map<String, dynamic>? _renewIntent;
  Map<String, dynamic>? _grant;
  String? _id;
  String? _key;
  String? _deviceId;
  String? _config;
  int _deadline = 0;
  int _intentAt = 0;
  int? _trafficBaseline;
  int? _lastTraffic;
  int? _renewTrafficAnchor;
  final InboundTrafficFreshness _inboundTraffic = InboundTrafficFreshness();
  bool _hasEstablishedHandshake = false;

  /// Task 7: the native side's own memory (NativeSnapshot.handshakeSeen). Diagnostics only:
  /// it never changes status, renewal or reconnect decisions.
  bool _nativeHandshakeSeen = false;
  bool _trafficRenewalRequested = false;
  @visibleForTesting
  bool get trafficRenewalRequested => _trafficRenewalRequested;
  @visibleForTesting
  bool get renewalPending => _renewIntent != null;
  int _version = 0;
  bool _closed = false;
  bool _working = false;
  bool _polling = false;
  bool _synced = false;
  bool _dead = false;
  Timer? _poll;
  Future<void>? _connectWork;
  Future<void>? _refreshWork;
  WindowNative get _native => native as WindowNative;
  @override
  bool get hasLease => _id != null || _createIntent != null;
  @override
  bool get busy => _working || _closed;
  @override
  bool get canRecover => false; // Unknown holds require operator reconciliation.
  void _emit() {
    if (!_dead) notifyListeners();
  }

  /// A new OTP session is a new server-side family even when this controller
  /// and the phone's locally held key survive a sign-out/sign-in cycle.
  void onAuthenticatedSessionChanged() {
    _deviceId = null;
    _synced = false;
    if (error == 'session_device_mismatch') {
      error = null;
      if (phase == ConnectionPhase.failed) phase = ConnectionPhase.disconnected;
    }
    _emit();
  }

  bool _cancelled(int version) => _dead || version != _version || _closed;

  @override
  void startPolling() {
    _poll ??= Timer.periodic(pollInterval, (_) => unawaited(refresh()));
  }

  @override
  void pausePolling() {
    _poll?.cancel();
    _poll = null;
  }

  @override
  Future<void> refreshNodes() async {
    if (nodesLoading || _dead) return;
    nodesLoading = true;
    _emit();
    try {
      // ADR-115: panel rows come only on request; the server adds them when enabled.
      final value = await api.call('GET', '$root/nodes?include=panel');
      final rows = value['nodes'];
      if (rows is! List || rows.length > maxCatalogNodes + maxPanelNodes)
        throw const ClientError('invalid_response');
      final parsed = <NodeOption>[];
      for (final row in rows) {
        if (row is! Map ||
            row['available'] is! bool ||
            row['name'] is! String ||
            row['region'] is! String)
          throw const ClientError('invalid_response');
        final id = _uuid(row['id']);
        if (parsed.any((n) => n.id == id))
          throw const ClientError('invalid_response');
        // Absent fields = an older API: a VPS node. Any other pairing is refused.
        final source = row['source'] ?? 'vps';
        final protocol = row['protocol'] ?? 'awg';
        final panel = source == panelServerSource;
        if (!(source == 'vps' && protocol == 'awg') &&
            !(panel && protocol == panelServerProtocol))
          throw const ClientError('invalid_response');
        final available = row['available'] as bool;
        parsed.add(
          NodeOption(
            id,
            row['name'] as String,
            row['region'] as String,
            panel && !panelVless
                ? panelUnsupportedReason
                : available
                ? 'available'
                : 'capacity',
            source: source as String,
            protocol: protocol as String,
            countryCode: parseCountryCode(row['country_code']),
            city: parseCity(row['city']),
            probeHost: row['probe_host'] is String && row['probe_port'] is int
                ? row['probe_host'] as String
                : null,
            probePort: row['probe_host'] is String && row['probe_port'] is int
                ? row['probe_port'] as int
                : null,
          ),
        );
      }
      nodes = parsed;
      nodesUpdatedAt = DateTime.parse(value['checked_at'] as String);
      nodesError = null;
      unawaited(measureLatency());
    } catch (e) {
      nodesError = e is ClientError ? e.code : 'invalid_response';
    } finally {
      nodesLoading = false;
      _emit();
    }
  }

  static String _uuid(dynamic value) {
    if (value is! String ||
        !RegExp(
          r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        ).hasMatch(value))
      throw const ClientError('invalid_response');
    return value;
  }

  /// After process death, stop native and revoke old device connections before admission.
  Future<void> _ensureDevice() async {
    if (_deviceId != null) return;
    diagnosticStage = 'device_key_read';
    _key ??= await native.publicKey();
    diagnosticStage = 'device_registration';
    final device = await api.call('POST', '/api/v1/devices', {
      'platform': devicePlatform,
      'key_type': 'x25519',
      'public_key': _key,
    });
    _deviceId = _uuid(device['id']);
  }

  /// After a UI restart: adopt the natively renewed tunnel still running in this process
  /// (ADR-117). Otherwise stop native and revoke this device's old connections before admission.
  /// Returns true if a running tunnel was adopted.
  Future<bool> _reconcile() async {
    if (_synced) return false;
    NativeSnapshot? live;
    try {
      live = await native.snapshot();
    } catch (_) {
      live = null;
    }
    final adoptable =
        live != null &&
        live.up &&
        live.nativeRenewal &&
        live.remainingMs > 0 &&
        live.connectionId != null &&
        live.windowGeneration != null;
    if (!adoptable) {
      diagnosticStage = 'native_disconnect';
      await native.disconnect();
    }
    diagnosticStage = 'connection_reconcile';
    final result = await api.call('GET', root);
    final rows = result['items'];
    if (rows is! List) throw const ClientError('invalid_response');
    for (final row in rows) {
      if (row is! Map) throw const ClientError('invalid_response');
    }
    if (adoptable) {
      final match = rows
          .where((row) => row['id'] == live!.connectionId)
          .firstOrNull;
      if (match is Map<String, dynamic> && _adopt(match, live)) {
        for (final row in rows) {
          if (identical(row, match) || row['state'] == 'closed') continue;
          try {
            await api.call('DELETE', '$root/${_uuid(row['id'])}');
          } catch (_) {
            /* Best effort; the server refuses a second live connection anyway. */
          }
        }
        _synced = true;
        return true;
      }
      diagnosticStage = 'native_disconnect';
      await native.disconnect();
      diagnosticStage = 'connection_reconcile';
    }
    for (final row in rows) {
      if (row['state'] != 'closed') {
        _id = _uuid(row['id']);
        remotePending = true;
        await _cleanup();
        if (hasLease) throw const ClientError('revoke_unconfirmed');
      }
    }
    _synced = true;
    return false;
  }

  /// ADR-117: take over a running tunnel only if the server's view of this device's connection
  /// matches what native holds; the same checks as [_validate] for a first grant.
  bool _adopt(Map<String, dynamic> value, NativeSnapshot live) {
    final raw = value['window'];
    if (value['state'] != 'active' ||
        raw is! Map<String, dynamic> ||
        value['id'] != live.connectionId ||
        value['device_id'] != _deviceId ||
        _key == null ||
        !_meters.contains(value['meter']) ||
        value['client_config'] is! String ||
        (value['client_config'] as String).length > 16384)
      return false;
    try {
      _uuid(value['id']);
      _uuid(value['node_id']);
      for (final field in [
        'connection_id',
        'window_id',
        'node_id',
        'runtime_id',
      ]) {
        _uuid(raw[field]);
      }
    } on ClientError {
      return false;
    }
    final generation = raw['generation'];
    final budget = raw['budget_bytes'];
    final slot = raw['slot_id'];
    if (raw['contract_version'] != 3 ||
        raw['connection_id'] != value['id'] ||
        raw['public_key'] != _key ||
        raw['node_id'] != value['node_id'] ||
        slot != value['slot_id'] ||
        generation is! int ||
        generation < live.windowGeneration! ||
        budget is! int ||
        budget < 65536 ||
        budget > 67108864 ||
        slot is! int ||
        slot < 1 ||
        slot > maxSlotId)
      return false;
    final now = _clock.elapsedMilliseconds;
    _id = value['id'] as String;
    _grant = raw;
    _config = value['client_config'] as String;
    selectedNode = value['node_id'] as String;
    _deadline = now + live.remainingMs;
    _intentAt = now;
    _renewIntent = null;
    _trafficBaseline = null;
    _lastTraffic = null;
    _renewTrafficAnchor = null;
    _inboundTraffic.reset();
    _hasEstablishedHandshake = false;
    _nativeHandshakeSeen = live.handshakeSeen;
    _connectedAtMs = now;
    remotePending = false;
    error = null;
    diagnosticStage = null;
    phase = ConnectionPhase.reconnecting;
    detail = 'VPN продолжает работать, проверяется связь';
    return true;
  }

  /// ADR-119: settle the previous connection and wait (bounded) until the server closed it.
  /// Returns false if the attempt was cancelled meanwhile.
  Future<bool> _closeOldLease(int version) async {
    diagnosticStage = 'old_lease_cleanup';
    await _cleanup();
    for (var i = 0; hasLease && i < revokeWaitChecks; i++) {
      if (_cancelled(version)) return false;
      await Future<void>.delayed(revokeWaitInterval);
      if (_cancelled(version)) return false;
      await _cleanup();
    }
    if (hasLease) throw const ClientError('revoke_unconfirmed');
    return !_cancelled(version);
  }

  @override
  Future<void> connect() async {
    if (busy || _polling || _dead) return;
    _working = true;
    final version = ++_version;
    _reconnectAfterCleanup = false;
    if (!_autoReconnecting) _activationRetries = 0;
    error = null;
    diagnosticStage = null;
    phase = ConnectionPhase.connecting;
    _emit();
    final work = _connect(version);
    _connectWork = work;
    try {
      await work;
    } finally {
      _connectWork = null;
      _working = false;
      _emit();
    }
  }

  Future<void> _connect(int version) async {
    try {
      diagnosticStage = 'selected_node';
      if (selectedNode == null) throw const ClientError('window_node_required');
      final chosen = nodes.where((n) => n.id == selectedNode).firstOrNull;
      if (chosen != null && chosen.isPanel) {
        await _connectPanel(version, chosen);
        return;
      }
      // A fresh OTP session has no device binding yet. Register this phone's
      // public key before asking the API to enumerate connections for it.
      await _ensureDevice();
      if (_cancelled(version)) return;
      if (await _reconcile()) {
        // ADR-117: the VPN was still running; keep it, the next refresh confirms it.
        startPolling();
        return;
      }
      if (_cancelled(version)) return;
      if (hasLease && !await _closeOldLease(version)) return;
      diagnosticStage = 'android_vpn_permission';
      if (!await native.permission())
        throw const ClientError('permission_denied');
      if (_cancelled(version)) return;
      diagnosticStage = 'key_challenge';
      final proof = await api.call(
        'POST',
        '/api/v1/tunnels/key-challenge/$_deviceId',
      );
      diagnosticStage = 'key_proof';
      final answer = await native.prove(
        proof['ephemeral_public_key'] as String,
        proof['transcript'] as String,
      );
      if (_cancelled(version)) return;
      _createIntent = {
        'device_id': _deviceId,
        'node_id': selectedNode,
        'idempotency_key': newId(),
        'proof_id': proof['id'],
        'proof_response': answer,
        'budget_bytes': budgetBytes,
        ..._windowRequest,
      };
      _intentAt = _clock.elapsedMilliseconds;
      diagnosticStage = 'lease_create';
      var value = await _create();
      for (var attempt = 0; value['state'] == 'pending'; attempt++) {
        if (_cancelled(version)) return;
        if (attempt >= 10) throw const ClientError('window_node_unavailable');
        await Future<void>.delayed(pollInterval);
        if (_cancelled(version)) return;
        diagnosticStage = 'lease_poll';
        value = await api.call('GET', '$root/$_id');
      }
      if (_cancelled(version)) return;
      diagnosticStage = 'lease_validation';
      final grant = _validate(value, initial: true);
      final deadline = _bound(grant);
      _config = value['client_config'] as String;
      diagnosticStage = 'native_tunnel_start';
      await _native.connectWindow(
        _config!,
        grant,
        deadline - _clock.elapsedMilliseconds,
      );
      if (_cancelled(version)) {
        await native.disconnect();
        return;
      }
      _grant = grant;
      _deadline = deadline;
      // A newly connected backend starts a new cumulative statistics lifetime.
      _trafficBaseline = 0;
      _lastTraffic = 0;
      _renewTrafficAnchor = null;
      _inboundTraffic.reset();
      _hasEstablishedHandshake = false;
      _nativeHandshakeSeen = false;
      _connectedAtMs = _clock.elapsedMilliseconds;
      _activationRetries = 0;
      diagnosticStage = null;
      diagnosticHandshake = null;
      detail = 'Ожидается handshake';
      await _startNativeRenewal(grant);
      if (_cancelled(version)) return;
      startPolling();
    } catch (e) {
      if (!_cancelled(version)) {
        error = e is ClientError
            ? e.code
            : e is PlatformException && _nativeStartErrorCodes.contains(e.code)
            ? e.code
            : 'native_unavailable';
        phase = ConnectionPhase.failed;
        await _cleanup();
        // ADR-119: the server did not activate a new connection (revoked or not acknowledged
        // in time). Retried a few times in a row, also after a manual connect.
        final activation = error == 'window_activation_refused';
        if (activation) _activationRetries++;
        // ADR-099: an automatic reconnect that hit a temporary failure keeps trying.
        if (activation
            ? _activationRetries <= maxActivationRetries
            : _autoReconnecting && _retryCodes.contains(error))
          _scheduleAutoReconnect(version);
      }
    }
  }

  /// ADR-115: a panel server. No lease, quota window or renewal: the panel meters its own
  /// client. The API re-checks the platform and the native side re-checks every field.
  Future<void> _connectPanel(int version, NodeOption node) async {
    final engine = native;
    if (!panelVless || engine is! VlessNative)
      throw const ClientError('panel_platform_unsupported');
    await _ensureDevice();
    if (_cancelled(version)) return;
    await _reconcile();
    if (_cancelled(version)) return;
    if (hasLease && !await _closeOldLease(version)) return;
    diagnosticStage = 'android_vpn_permission';
    if (!await native.permission())
      throw const ClientError('permission_denied');
    if (_cancelled(version)) return;
    diagnosticStage = 'panel_profile';
    final args = vlessProfileArgs(
      await api.call('GET', '$root/panel-servers/${node.id}/profile'),
      node.id,
    );
    if (args == null) throw const ClientError('invalid_response');
    if (_cancelled(version)) return;
    diagnosticStage = 'native_tunnel_start';
    // VlessNative is a separate interface, so the check above cannot promote `engine`.
    await (engine as VlessNative).connectVless(args, panelSessionMaxMs);
    if (_cancelled(version)) {
      await native.disconnect();
      return;
    }
    _panelUp = true;
    _connectedAtMs = _clock.elapsedMilliseconds;
    diagnosticStage = null;
    diagnosticHandshake = null;
    phase = ConnectionPhase.connected;
    detail = 'Подключено к серверу из панели';
    startPolling();
  }

  Future<Map<String, dynamic>> _create() async {
    try {
      final value = await api.call('POST', root, _createIntent);
      _id = _uuid(value['id']);
      _createIntent = null;
      return value;
    } on ClientError catch (e) {
      if (!{
        'network_unavailable',
        'api_unavailable',
        'invalid_response',
        'unauthorized',
        'session_expired',
      }.contains(e.code))
        _createIntent = null;
      rethrow;
    }
  }

  Map<String, dynamic> _validate(
    Map<String, dynamic> value, {
    bool initial = false,
    bool nativeRenewal = false,
  }) {
    final raw = value['window'];
    if (value['state'] != 'active' ||
        raw is! Map<String, dynamic> ||
        value['id'] != _id ||
        value['device_id'] != _deviceId ||
        !_meters.contains(value['meter']) ||
        value['client_config'] is! String ||
        (value['client_config'] as String).length > 16384)
      // ADR-119: a new connection the server did not activate is not a failed renewal.
      throw ClientError(
        initial ? 'window_activation_refused' : 'window_not_active',
      );
    for (final field in [
      'connection_id',
      'window_id',
      'node_id',
      'runtime_id',
    ]) {
      _uuid(raw[field]);
    }
    final generation = raw['generation'];
    if (raw['contract_version'] != 3 ||
        raw['connection_id'] != _id ||
        raw['public_key'] != _key ||
        raw['node_id'] != selectedNode ||
        raw['slot_id'] != value['slot_id'] ||
        value['node_id'] != selectedNode ||
        generation is! int ||
        raw['budget_bytes'] is! int ||
        (raw['budget_bytes'] as int) < 65536 ||
        (raw['budget_bytes'] as int) > 67108864 ||
        raw['slot_id'] is! int ||
        (raw['slot_id'] as int) < 1 ||
        (raw['slot_id'] as int) > maxSlotId)
      throw const ClientError('invalid_response');
    if (initial) {
      if (generation != 1) throw const ClientError('invalid_response');
    } else {
      final old = _grant!;
      if (raw['runtime_id'] != old['runtime_id'] ||
          raw['slot_id'] != old['slot_id'] ||
          value['client_config'] != _config ||
          (nativeRenewal
              ? generation < (old['generation'] as int)
              : generation != old['generation'] &&
                    generation != old['generation'] + 1))
        throw const ClientError('window_generation_conflict');
      if (generation == old['generation']) {
        if (!mapEquals(raw, old))
          throw const ClientError('window_generation_conflict');
      } else if (!nativeRenewal &&
          raw['window_id'] != _renewIntent?['idempotency_key']) {
        throw const ClientError('window_generation_conflict');
      }
    }
    return raw;
  }

  /// ADR-097: hand renewal to the native VPN process for long windows. Any failure (old API,
  /// platform without a renewer) silently keeps Dart-driven renewal; it never stops the tunnel.
  Future<void> _startNativeRenewal(Map<String, dynamic> grant) async {
    final tunnel = native;
    final host = tunnel is NativeRenewalHost
        ? tunnel as NativeRenewalHost
        : null;
    final client = api;
    final origin = _renewalOrigin ?? (client is Api ? client.origin : null);
    if (windowSeconds == 90 || host == null || origin == null) {
      return;
    }
    try {
      final issued = await api.call('POST', '$root/$_id/renew-token');
      final token = issued['renew_token'];
      if (token is! String) return;
      await host.startNativeRenewal({
        'origin': origin.toString(),
        'token': token,
        'connection': grant['connection_id'],
        'runtime': grant['runtime_id'],
        'windowId': grant['window_id'],
        'generation': grant['generation'],
        'budgetBytes': budgetBytes,
        'windowSeconds': windowSeconds,
      });
    } catch (_) {
      // ADR-117: any failure keeps Dart renewal, including MissingPluginException from an
      // engine that does not offer native renewal. It must never fail a connect.
      return;
    }
  }

  int _bound(Map<String, dynamic> grant) {
    final issued = DateTime.parse(grant['issued_at'] as String);
    final expiry = DateTime.parse(grant['not_after'] as String);
    final duration = expiry.difference(issued).inMilliseconds;
    if (!issued.isUtc ||
        !expiry.isUtc ||
        duration <= 6000 ||
        duration > maxWindowMs ||
        issued.isAfter(DateTime.now().toUtc().add(const Duration(seconds: 1))))
      throw const ClientError('invalid_response');
    final deadline = min(
      _intentAt + duration - 6000,
      _clock.elapsedMilliseconds +
          expiry.difference(DateTime.now().toUtc()).inMilliseconds -
          6000,
    );
    if (deadline <= _clock.elapsedMilliseconds)
      throw const ClientError('lease_expired');
    return deadline;
  }

  Future<void> _cleanup() async {
    await native.disconnect();
    _panelUp = false;
    _grant = null;
    _config = null;
    _renewIntent = null;
    _trafficBaseline = null;
    _lastTraffic = null;
    _renewTrafficAnchor = null;
    _inboundTraffic.reset();
    _hasEstablishedHandshake = false;
    try {
      if (_createIntent != null) await _create();
      if (_id != null) {
        final value = await api.call('DELETE', '$root/$_id');
        if (value['state'] == 'closed') _id = null;
      }
    } catch (_) {
      /* Retain the original admission intent until closure. */
    }
    remotePending = hasLease;
    if (remotePending) {
      error ??= 'revoke_unconfirmed';
      startPolling();
    } else if (error == 'revoke_unconfirmed') {
      error = null;
    }
    unawaited(refreshQuota());
  }

  @override
  Future<void> disconnect() async {
    if (_closed) return;
    _closed = true;
    ++_version;
    _reconnectAfterCleanup = false;
    _emit();
    try {
      await native.disconnect();
      await _connectWork;
      await _refreshWork;
      await _cleanup();
      phase = ConnectionPhase.disconnected;
      detail = 'VPN на телефоне выключен';
      if (!remotePending) error = null;
    } catch (_) {
      error = 'native_disconnect_failed';
      phase = ConnectionPhase.failed;
    } finally {
      _closed = false;
      _emit();
    }
  }

  @override
  Future<void> refresh() async {
    if (_polling || busy || _dead) return;
    _polling = true;
    final work = _refresh(_version);
    _refreshWork = work;
    try {
      await work;
    } finally {
      _refreshWork = null;
      _polling = false;
      _emit();
    }
  }

  bool get _handshakeOverdue =>
      !_hasEstablishedHandshake &&
      _clock.elapsedMilliseconds - _connectedAtMs >=
          handshakeHintAfter.inMilliseconds;

  Future<void> _refresh(int version) async {
    try {
      await _ensureDevice();
      if (_cancelled(version)) return;
      if (remotePending) {
        await _cleanup();
        // ADR-119: the stopped connection finished closing; now the reconnect can start.
        if (!hasLease && _reconnectAfterCleanup && !_cancelled(version)) {
          _reconnectAfterCleanup = false;
          _scheduleAutoReconnect(version);
        }
        return;
      }
      // ADR-115: a panel session has no lease to reconcile; only the engine's liveness.
      if (_panelUp) {
        final snapshot = await native.snapshot();
        if (_cancelled(version)) return;
        if (!snapshot.up) throw ClientError(_stopCode(snapshot));
        return;
      }
      // Fresh OTP sessions are not bound to a device until this phone registers
      // its local public key. Bind first so the per-device reconciliation query
      // can safely return this phone's prior connections (if any).
      await _reconcile();
      if (_cancelled(version) || _id == null) return;
      var snapshot = await native.snapshot();
      if (_cancelled(version)) return;
      // Read before any stop check below, so a window that just ended still reports it.
      if (snapshot.handshakeSeen) _nativeHandshakeSeen = true;
      // While native renews, its deadline is authoritative: Dart timers may have slept.
      if (snapshot.nativeRenewal && snapshot.up && snapshot.remainingMs > 0)
        _deadline = _clock.elapsedMilliseconds + snapshot.remainingMs;
      if (_clock.elapsedMilliseconds >= _deadline ||
          !snapshot.up ||
          snapshot.remainingMs <= 0)
        throw ClientError(_stopCode(snapshot));
      _inboundTraffic.observe(
        snapshot.receivedBytes,
        _clock.elapsedMilliseconds,
      );
      if (snapshot.confirmed) _hasEstablishedHandshake = true;
      final traffic = snapshot.trafficBytes;
      if (traffic != null && traffic >= 0) {
        if (_trafficBaseline == null ||
            (_lastTraffic != null && traffic < _lastTraffic!)) {
          // Missing/reset stats are advisory only; start a fresh baseline.
          _trafficBaseline = traffic;
          _renewTrafficAnchor = null;
        }
        _lastTraffic = traffic;
      }
      final trafficSuggestsRenewal =
          traffic != null &&
          traffic >= 0 &&
          _trafficBaseline != null &&
          traffic >= _trafficBaseline! &&
          traffic - _trafficBaseline! >= (_grant!['budget_bytes'] as int) ~/ 2;
      if (_renewIntent == null &&
          !snapshot.nativeRenewal &&
          (_deadline - _clock.elapsedMilliseconds <= _renewBefore ||
              trafficSuggestsRenewal)) {
        if (trafficSuggestsRenewal &&
            _deadline - _clock.elapsedMilliseconds > _renewBefore) {
          _trafficRenewalRequested = true;
        }
        _renewIntent = {
          'previous_window_id': _grant!['window_id'],
          'idempotency_key': newId(),
          'budget_bytes': budgetBytes,
          ..._windowRequest,
        };
        _intentAt = _clock.elapsedMilliseconds;
        // Capture before issuing the request: bytes sent during rotation are
        // counted conservatively in the next window's advisory estimate.
        _renewTrafficAnchor = traffic != null && traffic >= 0 ? traffic : null;
      }
      var value = await api.call('GET', '$root/$_id');
      if (_cancelled(version)) return;
      if (_renewIntent != null) {
        // Same immutable request on response loss. Only GET active ACK extends native.
        await api.call('POST', '$root/$_id/renew', _renewIntent);
        if (_cancelled(version)) return;
        value = await api.call('GET', '$root/$_id');
      }
      if (_cancelled(version)) return;
      if (_clock.elapsedMilliseconds >= _deadline)
        throw const ClientError('lease_expired');
      if (value['state'] == 'pending' &&
          (_renewIntent != null || snapshot.nativeRenewal)) {
        final confirmed = _establishedStatusConfirmed(snapshot);
        phase = confirmed
            ? ConnectionPhase.connected
            : ConnectionPhase.reconnecting;
        detail = confirmed
            ? (snapshot.confirmed
                  ? 'Свежий handshake подтверждён'
                  : 'Входящий трафик через VPN подтверждён')
            : 'Ожидается подтверждение продления';
        return;
      }
      final grant = _validate(value, nativeRenewal: snapshot.nativeRenewal);
      if (grant['generation'] != _grant!['generation'] &&
          snapshot.nativeRenewal) {
        // Native already advanced its own deadline (possibly several generations while the app
        // slept); adopt the grant and keep mirroring the native deadline.
        _grant = grant;
        _trafficBaseline = traffic;
        _renewTrafficAnchor = null;
        _renewIntent = null;
      } else if (grant['generation'] != _grant!['generation']) {
        final end = _bound(grant);
        if (end <= _deadline)
          throw const ClientError('window_generation_conflict');
        await _native.advanceWindow(grant, end - _clock.elapsedMilliseconds);
        if (_cancelled(version)) return;
        _grant = grant;
        _deadline = end;
        _trafficBaseline =
            _renewTrafficAnchor != null &&
                traffic != null &&
                traffic >= _renewTrafficAnchor!
            ? _renewTrafficAnchor
            : traffic;
        _renewTrafficAnchor = null;
        _renewIntent = null;
      }
      snapshot = await native.snapshot();
      if (_cancelled(version)) return;
      if (!snapshot.up ||
          snapshot.remainingMs <= 0 ||
          _clock.elapsedMilliseconds >= _deadline)
        throw ClientError(_stopCode(snapshot));
      error = null;
      final confirmed = _establishedStatusConfirmed(snapshot);
      phase = confirmed
          ? ConnectionPhase.connected
          : ConnectionPhase.reconnecting;
      detail = snapshot.confirmed
          ? 'Свежий handshake подтверждён'
          : confirmed
          ? 'Входящий трафик через VPN подтверждён'
          : _handshakeOverdue
          ? 'Handshake не получен более ${handshakeHintAfter.inSeconds} с. '
                'Нажмите «Отключить» и подключитесь заново.'
          : 'Ожидается сеть и handshake';
    } catch (e) {
      if (_cancelled(version)) return;
      // API shape failures are raised as ClientError('invalid_response'). Any
      // other exception here can come from the native bridge (for example,
      // reading the app-private device key) and must not be mislabeled as an
      // API response failure.
      final code = e is ClientError ? e.code : 'native_unavailable';
      error = code;
      // Task 7: recorded before cleanup resets it. The native memory also counts a handshake
      // that happened while the app was asleep (Android VPN process, Windows controller).
      diagnosticHandshake = _hasEstablishedHandshake || _nativeHandshakeSeen
          ? 'confirmed'
          : 'not_seen';
      phase = ConnectionPhase.reconnecting;
      if (!{
            'network_unavailable',
            'api_unavailable',
            'rate_limited',
          }.contains(code) ||
          (_grant != null && _clock.elapsedMilliseconds >= _deadline)) {
        phase = ConnectionPhase.failed;
        await _cleanup();
        if (_autoReconnectCodes.contains(code)) {
          if (!hasLease) {
            _scheduleAutoReconnect(version);
          } else {
            _reconnectAfterCleanup = true;
          }
        }
      }
    }
  }

  /// Window ran out, the server no longer treats it as active (a late renewal), or the phone
  /// dropped the tunnel while time was left (failed in-place restart, killed service):
  /// reconnecting is the only recovery. Lost VPN consent ('vpn_revoked') is never undone.
  static const _autoReconnectCodes = {
    'lease_expired',
    'window_not_active',
    'native_down',
  };

  /// Temporary failures of an automatic reconnect attempt itself; it is retried with backoff.
  static const _retryCodes = {
    ..._autoReconnectCodes,
    'network_unavailable',
    'api_unavailable',
    'rate_limited',
    'window_node_unavailable',
    'window_activation_refused',
    // A create with an unknown outcome must be settled first; the attempt fails fast without
    // an API call until the background settlement finishes, then the next attempt connects.
    'revoke_unconfirmed',
  };
  bool _autoReconnecting = false;

  /// Backoff multipliers of [autoReconnectDelay] by attempts in the last 10 minutes
  /// (2 s, 5 s, 15 s, 30 s, 60 s, then every 2 min with the default delay).
  static const _backoff = [1, 2.5, 7.5, 15, 30, 60];

  /// Why the tunnel is gone. Only a window that really ran out is 'lease_expired'; if the phone
  /// stopped the VPN while time was left (another VPN, system toggle, service restart) say so.
  String _stopCode(NativeSnapshot snapshot) =>
      !snapshot.up && snapshot.reason == 'vpn_revoked'
      ? 'vpn_revoked'
      : !snapshot.up &&
            snapshot.reason != 'lease_expired' &&
            _clock.elapsedMilliseconds < _deadline - 1000
      ? 'native_down'
      : 'lease_expired';

  /// The tunnel ended without the user pressing Disconnect: connect again on our own with
  /// backoff and no hard limit (ADR-099). A failed attempt reserves no quota; a user disconnect
  /// bumps the version and cancels every pending attempt.
  void _scheduleAutoReconnect(int version) {
    final now = _clock.elapsedMilliseconds;
    _autoReconnectAt.removeWhere((t) => now - t > 600000);
    final base = autoReconnectDelay;
    if (base == null) return;
    final step = _backoff[min(_autoReconnectAt.length, _backoff.length - 1)];
    _autoReconnectAt.add(now);
    final delay = Duration(microseconds: (base.inMicroseconds * step).round());
    Future<void> fire() async {
      if (_dead ||
          _version != version ||
          phase != ConnectionPhase.failed ||
          !_retryCodes.contains(error)) {
        return;
      }
      // ADR-119: a refresh in flight would make connect() return without trying; wait for it.
      if (_working || _polling) {
        unawaited(Future<void>.delayed(base, fire));
        return;
      }
      _autoReconnecting = true;
      try {
        await connect();
      } finally {
        _autoReconnecting = false;
      }
    }

    unawaited(Future<void>.delayed(delay, fire));
  }

  /// ADR-119: choose another server. While connected or connecting: stop, settle the old
  /// connection (a pending create keeps its idempotency key), then connect to the new server.
  /// A switch while another one runs is ignored.
  Future<void> switchNode(String id) async {
    if (_dead || _switching) return;
    if (!nodes.any((n) => n.id == id && n.reason == 'available')) return;
    final live =
        hasLease ||
        busy ||
        phase == ConnectionPhase.connecting ||
        phase == ConnectionPhase.connected ||
        phase == ConnectionPhase.reconnecting;
    if (!live) {
      selectNode(id);
      return;
    }
    if (id == selectedNode && hasLease && phase != ConnectionPhase.failed)
      return;
    _switching = true;
    try {
      await disconnect();
      if (_dead) return;
      await _refreshWork;
      if (_dead) return;
      // The old connection may still be closing; connect() waits for that (bounded).
      selectedNode = id;
      _emit();
      await connect();
    } finally {
      _switching = false;
    }
  }

  bool _establishedStatusConfirmed(NativeSnapshot snapshot) {
    if (snapshot.confirmed) _hasEstablishedHandshake = true;
    return snapshot.confirmed ||
        (_hasEstablishedHandshake &&
            snapshot.up &&
            snapshot.network &&
            snapshot.remainingMs > 0 &&
            _inboundTraffic.observe(
              snapshot.receivedBytes,
              _clock.elapsedMilliseconds,
            ));
  }

  @override
  void dispose() {
    _dead = true;
    ++_version;
    pausePolling();
    super.dispose();
  }
}

const _nativeStartErrorCodes = <String>{
  'native_config_invalid',
  'native_engine_unavailable',
  'native_io_error',
  'native_permission_error',
  'native_service_state_invalid',
  'native_start_failed',
};

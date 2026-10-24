import 'dart:async';
import 'dart:math';
import 'package:flutter/foundation.dart';

import 'api.dart';
import 'connection.dart';
import 'native_tunnel.dart';

/// Explicit v3 selection, never falls back to the bounded v2 protocol.
class WindowConnectionController extends ConnectionController {
  WindowConnectionController(
    super.api,
    WindowNative super.native, {
    super.pollInterval,
    this.renewBeforeMs = 55000,
  });
  static const root = '/api/v1/window-tunnels';
  final int renewBeforeMs;
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
      final value = await api.call('GET', '$root/nodes');
      final rows = value['nodes'];
      if (rows is! List || rows.length > 16)
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
        parsed.add(
          NodeOption(
            id,
            row['name'] as String,
            row['region'] as String,
            (row['available'] as bool) ? 'available' : 'capacity',
          ),
        );
      }
      nodes = parsed;
      nodesUpdatedAt = DateTime.parse(value['checked_at'] as String);
      nodesError = null;
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
  Future<void> _reconcile() async {
    if (_synced) return;
    await native.disconnect();
    final result = await api.call('GET', root);
    final rows = result['items'];
    if (rows is! List) throw const ClientError('invalid_response');
    for (final row in rows) {
      if (row is! Map) throw const ClientError('invalid_response');
      if (row['state'] != 'closed') {
        _id = _uuid(row['id']);
        remotePending = true;
        await _cleanup();
        if (hasLease) throw const ClientError('revoke_unconfirmed');
      }
    }
    _synced = true;
  }

  @override
  Future<void> connect() async {
    if (busy || _polling || _dead) return;
    _working = true;
    final version = ++_version;
    error = null;
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
      await _reconcile();
      if (_cancelled(version)) return;
      if (hasLease) {
        await _cleanup();
        if (hasLease) throw const ClientError('revoke_unconfirmed');
      }
      if (selectedNode == null) throw const ClientError('window_node_required');
      if (!await native.permission())
        throw const ClientError('permission_denied');
      if (_cancelled(version)) return;
      _key = await native.publicKey();
      final device = await api.call('POST', '/api/v1/devices', {
        'platform': 'android',
        'key_type': 'x25519',
        'public_key': _key,
      });
      if (_cancelled(version)) return;
      _deviceId = _uuid(device['id']);
      final proof = await api.call(
        'POST',
        '/api/v1/tunnels/key-challenge/${_uuid(device['id'])}',
      );
      final answer = await native.prove(
        proof['ephemeral_public_key'] as String,
        proof['transcript'] as String,
      );
      if (_cancelled(version)) return;
      _createIntent = {
        'device_id': device['id'],
        'node_id': selectedNode,
        'idempotency_key': newId(),
        'proof_id': proof['id'],
        'proof_response': answer,
        'budget_bytes': 16777216,
      };
      _intentAt = _clock.elapsedMilliseconds;
      var value = await _create();
      for (var attempt = 0; value['state'] == 'pending'; attempt++) {
        if (_cancelled(version)) return;
        if (attempt >= 10) throw const ClientError('window_node_unavailable');
        await Future<void>.delayed(pollInterval);
        if (_cancelled(version)) return;
        value = await api.call('GET', '$root/$_id');
      }
      if (_cancelled(version)) return;
      final grant = _validate(value, initial: true);
      final deadline = _bound(grant);
      _config = value['client_config'] as String;
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
      detail = 'Ожидается handshake';
      startPolling();
    } catch (e) {
      if (!_cancelled(version)) {
        error = e is ClientError ? e.code : 'native_unavailable';
        phase = ConnectionPhase.failed;
        await _cleanup();
      }
    }
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
  }) {
    final raw = value['window'];
    if (value['state'] != 'active' ||
        raw is! Map<String, dynamic> ||
        value['id'] != _id ||
        value['device_id'] != _deviceId ||
        value['meter'] != 'kernel-outer-ip.v1' ||
        value['client_config'] is! String ||
        (value['client_config'] as String).length > 16384)
      throw const ClientError('window_not_active');
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
        (raw['slot_id'] as int) > 16)
      throw const ClientError('invalid_response');
    if (initial) {
      if (generation != 1) throw const ClientError('invalid_response');
    } else {
      final old = _grant!;
      if (raw['runtime_id'] != old['runtime_id'] ||
          raw['slot_id'] != old['slot_id'] ||
          value['client_config'] != _config ||
          (generation != old['generation'] &&
              generation != old['generation'] + 1))
        throw const ClientError('window_generation_conflict');
      if (generation == old['generation']) {
        if (!mapEquals(raw, old))
          throw const ClientError('window_generation_conflict');
      } else if (raw['window_id'] != _renewIntent?['idempotency_key']) {
        throw const ClientError('window_generation_conflict');
      }
    }
    return raw;
  }

  int _bound(Map<String, dynamic> grant) {
    final issued = DateTime.parse(grant['issued_at'] as String);
    final expiry = DateTime.parse(grant['not_after'] as String);
    final duration = expiry.difference(issued).inMilliseconds;
    if (!issued.isUtc ||
        !expiry.isUtc ||
        duration <= 6000 ||
        duration > 90000 ||
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
    _grant = null;
    _config = null;
    _renewIntent = null;
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
      error = 'revoke_unconfirmed';
      startPolling();
    }
    unawaited(refreshQuota());
  }

  @override
  Future<void> disconnect() async {
    if (_closed) return;
    _closed = true;
    ++_version;
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

  Future<void> _refresh(int version) async {
    try {
      if (remotePending) {
        await _cleanup();
        return;
      }
      await _reconcile();
      if (_cancelled(version) || _id == null) return;
      var snapshot = await native.snapshot();
      if (_cancelled(version)) return;
      if (_clock.elapsedMilliseconds >= _deadline ||
          !snapshot.up ||
          snapshot.remainingMs <= 0)
        throw const ClientError('lease_expired');
      if (_renewIntent == null &&
          _deadline - _clock.elapsedMilliseconds <= renewBeforeMs) {
        _renewIntent = {
          'previous_window_id': _grant!['window_id'],
          'idempotency_key': newId(),
          'budget_bytes': 16777216,
        };
        _intentAt = _clock.elapsedMilliseconds;
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
      if (value['state'] == 'pending' && _renewIntent != null) {
        phase = ConnectionPhase.reconnecting;
        detail = 'Ожидается подтверждение продления';
        return;
      }
      final grant = _validate(value);
      if (grant['generation'] != _grant!['generation']) {
        final end = _bound(grant);
        if (end <= _deadline)
          throw const ClientError('window_generation_conflict');
        await _native.advanceWindow(grant, end - _clock.elapsedMilliseconds);
        if (_cancelled(version)) return;
        _grant = grant;
        _deadline = end;
        _renewIntent = null;
      }
      snapshot = await native.snapshot();
      if (_cancelled(version)) return;
      if (!snapshot.up ||
          snapshot.remainingMs <= 0 ||
          _clock.elapsedMilliseconds >= _deadline)
        throw const ClientError('lease_expired');
      error = null;
      phase = snapshot.confirmed
          ? ConnectionPhase.connected
          : ConnectionPhase.reconnecting;
      detail = snapshot.confirmed
          ? 'Свежий handshake подтверждён'
          : 'Ожидается сеть и handshake';
    } catch (e) {
      if (_cancelled(version)) return;
      final code = e is ClientError ? e.code : 'invalid_response';
      error = code;
      phase = ConnectionPhase.reconnecting;
      if (!{
            'network_unavailable',
            'api_unavailable',
            'rate_limited',
          }.contains(code) ||
          (_grant != null && _clock.elapsedMilliseconds >= _deadline)) {
        phase = ConnectionPhase.failed;
        await _cleanup();
      }
    }
  }

  @override
  void dispose() {
    _dead = true;
    ++_version;
    pausePolling();
    super.dispose();
  }
}

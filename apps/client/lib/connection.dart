import 'dart:async';

import 'package:flutter/foundation.dart';

import 'api.dart';
import 'native_tunnel.dart';
import 'quota.dart';

enum ConnectionPhase {
  disconnected,
  connecting,
  connected,
  reconnecting,
  failed,
}

String messageFor(String code) => switch (code) {
  'permission_denied' =>
    'Разрешение VPN не выдано. Нажмите «Подключить» и подтвердите запрос Android.',
  'insufficient_quota' =>
    'Недостаточно свободного трафика для тестового подключения.',
  'device_revoked' ||
  'session_expired' ||
  'unauthorized' => 'Доступ завершён. Отключите VPN и войдите снова.',
  'device_limit_reached' =>
    'Достигнут лимит устройств. Отзовите старое устройство в аккаунте.',
  'session_device_mismatch' =>
    'Сессия принадлежит другому ключу. Войдите снова на этом устройстве.',
  'device_conflict' =>
    'Ключ связан с другим аккаунтом или отозван. Остановите приложение в настройках Android и войдите снова.',
  'tunnel_node_capacity' => 'Тестовый узел занят. Повторите позже.',
  'tunnel_recovery_not_ready' =>
    'Завершение пока запрещено: дождитесь истечения lease и подтверждения недоступности узла.',
  'tunnel_node_unavailable' ||
  'tunnel_profile_unavailable' ||
  'admission_closed' =>
    'Тестовый узел не готов. Повторите после запуска staging.',
  'lease_expired' =>
    'Срок тестового подключения истёк. После завершения отзыва можно подключиться снова.',
  'remote_revoked' =>
    'Доступ к туннелю отозван сервером. VPN отключён на телефоне.',
  'revoke_unconfirmed' =>
    'VPN на телефоне выключен. Отзыв на сервере ещё не подтверждён; повторная проверка выполняется автоматически.',
  'native_disconnect_failed' =>
    'Не удалось подтвердить отключение. Выключите VPN в системных настройках Android.',
  'network_unavailable' || 'api_unavailable' =>
    'Нет связи с API. Проверьте сеть и повторите. Состояние узла не подтверждено.',
  'native_unavailable' => 'VPN-движок недоступен. Проверьте сборку приложения.',
  'native_down' =>
    'Системный VPN остановлен. Проверьте разрешение или другой VPN и подключитесь снова.',
  'rate_limited' => 'Слишком много запросов. Подождите перед повтором.',
  _ =>
    'Подключение не выполнено. Проверьте сеть, доступ и готовность тестового узла.',
};

String nodeMessage(String reason) => switch (reason) {
  'available' => 'Готов принять подключение',
  'disabled' => 'Выключен оператором',
  'heartbeat_stale' => 'Нет свежего ответа агента',
  'runtime_mismatch' => 'Движок не подтверждён',
  'capacity' => 'Тестовый узел занят',
  _ => 'Состояние неизвестно',
};

class NodeOption {
  NodeOption(this.id, this.name, this.region, this.reason);
  final String id;
  final String name;
  final String region;
  final String reason;
}

/// One create intent at a time. Cancellation stops native first and reconciles
/// an ambiguous POST with its original idempotency key before another connect.
class ConnectionController extends ChangeNotifier {
  ConnectionController(
    this.api,
    this.native, {
    this.pollInterval = const Duration(seconds: 2),
  }) : quota = QuotaController(api) {
    quota.addListener(_emit);
  }
  final TunnelApi api;
  final NativeTunnel native;
  final Duration pollInterval;
  ConnectionPhase phase = ConnectionPhase.disconnected;
  String? error;
  String detail = 'VPN выключен';
  final QuotaController quota;
  int? get availableBytes => quota.snapshot?.available;
  DateTime? get quotaUpdatedAt => quota.updatedAt;
  bool get quotaStale => quota.stale;
  bool remotePending = false;
  List<NodeOption> nodes = [];
  String? selectedNode;
  String? nodesError;
  bool nodesLoading = false;
  DateTime? nodesUpdatedAt;
  bool get canRecover =>
      remotePending &&
      _tunnel != null &&
      !busy &&
      !_refreshing &&
      _cleaning == null;

  void selectNode(String? id) {
    if (busy || hasLease) return;
    if (id != null && !nodes.any((n) => n.id == id && n.reason == 'available'))
      return;
    selectedNode = id;
    _emit();
  }

  Future<void> refreshNodes() async {
    if (nodesLoading || _disposed) return;
    nodesLoading = true;
    _emit();
    try {
      final response = await api.call('GET', '/api/v1/nodes');
      final raw = response['nodes'];
      if (raw is! List || raw.length > 2)
        throw const ClientError('invalid_response');
      final parsed = <NodeOption>[];
      for (final row in raw) {
        if (row is! Map ||
            row['id'] is! String ||
            row['name'] is! String ||
            row['region'] is! String ||
            !{
              'available',
              'disabled',
              'heartbeat_stale',
              'runtime_mismatch',
              'capacity',
            }.contains(row['reason']))
          throw const ClientError('invalid_response');
        final id = row['id'] as String;
        if (!RegExp(r'^[0-9a-fA-F-]{36}$').hasMatch(id) ||
            parsed.any((n) => n.id == id))
          throw const ClientError('invalid_response');
        parsed.add(
          NodeOption(
            id,
            row['name'] as String,
            row['region'] as String,
            row['reason'] as String,
          ),
        );
      }
      final checked = DateTime.tryParse(response['checked_at'] as String);
      if (checked == null) throw const ClientError('invalid_response');
      if (_disposed) return;
      nodes = parsed;
      nodesUpdatedAt = checked;
      nodesError = null;
    } catch (failure) {
      if (!_disposed)
        nodesError = failure is ClientError ? failure.code : 'invalid_response';
    } finally {
      nodesLoading = false;
      _emit();
    }
  }

  Future<void> recoverExpired() async {
    if (!canRecover) return;
    final tunnelId = _tunnel;
    _closing = true;
    ++_epoch;
    _emit();
    try {
      await native.disconnect();
      final response = await api.call(
        'POST',
        '/api/v1/tunnels/$tunnelId/recover',
      );
      if (response['state'] != 'revoked')
        throw const ClientError('invalid_response');
      _tunnel = null;
      remotePending = false;
      error = null;
      phase = ConnectionPhase.disconnected;
      detail = 'Старый lease завершён. Можно выбрать другой узел';
      unawaited(refreshQuota());
      unawaited(refreshNodes());
    } catch (failure) {
      error = failure is ClientError
          ? failure.code
          : 'native_disconnect_failed';
    } finally {
      _closing = false;
      _emit();
    }
  }

  bool _revokeSent = false;
  String? _device;
  String? _tunnel;
  Map<String, dynamic>? _pendingBody;
  Stopwatch? _leaseClock;
  int _leaseLimitMs = 0;
  int _epoch = 0;
  bool _disposed = false;
  bool _refreshing = false;
  bool _closing = false;
  Future<void>? _cleaning;
  Future<void>? _connecting;
  Timer? _timer;
  bool get busy => _connecting != null || _closing;
  bool get hasLease => _tunnel != null || _pendingBody != null;
  void _emit() {
    if (!_disposed) notifyListeners();
  }

  void startPolling() {
    _timer ??= Timer.periodic(pollInterval, (_) => unawaited(refresh()));
  }

  void pausePolling() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> refreshQuota() async {
    await quota.refresh();
  }

  Future<void> connect() async {
    if (busy) return;
    final epoch = ++_epoch;
    error = null;
    phase = ConnectionPhase.connecting;
    detail = 'Ожидается разрешение Android';
    final work = _connect(epoch);
    _connecting = work;
    _emit();
    try {
      await work;
    } finally {
      _connecting = null;
      unawaited(refreshQuota());
      _emit();
    }
  }

  bool _cancelled(int epoch) => epoch != _epoch || _disposed;
  Future<void> _connect(int epoch) async {
    try {
      if (hasLease) {
        await _cleanup();
        if (hasLease) throw const ClientError('revoke_unconfirmed');
      }
      if (!await native.permission())
        throw const ClientError('permission_denied');
      if (_cancelled(epoch)) return;
      detail = 'Проверка устройства и доступа';
      _emit();
      final device = await api.call('POST', '/api/v1/devices', {
        'platform': 'android',
        'key_type': 'x25519',
        'public_key': await native.publicKey(),
      });
      _device = device['id'] as String;
      if (_cancelled(epoch)) return;
      final proof = await api.call(
        'POST',
        '/api/v1/tunnels/key-challenge/$_device',
      );
      final answer = await native.prove(
        proof['ephemeral_public_key'] as String,
        proof['transcript'] as String,
      );
      if (_cancelled(epoch)) return;
      _pendingBody = {
        'device_id': _device,
        'idempotency_key': newId(),
        'proof_id': proof['id'],
        'proof_response': answer,
        'budget_bytes': 1048576,
        if (selectedNode != null) 'node_id': selectedNode,
      };
      _leaseClock = Stopwatch()..start();
      final tunnel = await _create();
      if (_cancelled(epoch)) return;
      detail = 'Ожидается подтверждение узла';
      _emit();
      var current = tunnel;
      // Bounded wait. Never create another lease while this one is pending.
      for (var attempt = 0; current['state'] != 'active'; attempt++) {
        if (_cancelled(epoch)) return;
        if (current['state'] == 'admission_pending')
          throw const ClientError('admission_closed');
        if (current['state'] != 'provision_pending')
          throw const ClientError('remote_revoked');
        if (attempt >= 10) throw const ClientError('tunnel_node_unavailable');
        await Future<void>.delayed(pollInterval);
        if (_cancelled(epoch)) return;
        current = await api.call('GET', '/api/v1/tunnels/$_tunnel');
      }
      final config = await api.call('GET', '/api/v1/tunnels/$_tunnel/config');
      if (_cancelled(epoch)) return;
      if (config['tunnel_id'] != _tunnel)
        throw const ClientError('invalid_response');
      final expires = DateTime.parse(config['expires_at'] as String).toUtc();
      // Conservative bounds: local wall clock AND monotonic time since POST.
      final sinceCreate = _leaseClock!.elapsedMilliseconds;
      final wallRemaining = expires
          .difference(DateTime.now().toUtc())
          .inMilliseconds;
      final remaining = wallRemaining < 60000 - sinceCreate
          ? wallRemaining
          : 60000 - sinceCreate;
      if (remaining <= 0) throw const ClientError('lease_expired');
      _leaseLimitMs = sinceCreate + remaining;
      await native.connect(config['config'] as String, remaining);
      if (_cancelled(epoch)) {
        await native.disconnect();
        return;
      }
      detail = 'Туннель создан, ожидается handshake';
      phase = ConnectionPhase.connecting;
      startPolling();
    } catch (failure) {
      if (!_cancelled(epoch)) {
        error = failure is ClientError ? failure.code : 'native_unavailable';
        phase = ConnectionPhase.failed;
        detail = 'Подключение не выполнено';
        await _cleanup();
        unawaited(refreshNodes());
      }
    }
  }

  Future<Map<String, dynamic>> _create() async {
    try {
      final response = await api.call('POST', '/api/v1/tunnels', _pendingBody);
      _tunnel = response['id'] as String;
      _revokeSent = false;
      _pendingBody = null;
      return response;
    } on ClientError catch (e) {
      if (!{
        'network_unavailable',
        'invalid_response',
        'api_unavailable',
        'session_expired',
        'unauthorized',
      }.contains(e.code)) {
        _pendingBody = null; // Definite rejection, transaction did not commit.
      }
      rethrow;
    }
  }

  Future<void> _cleanup() async {
    final work = _cleaning ??= _performCleanup();
    try {
      await work;
    } finally {
      if (identical(_cleaning, work)) _cleaning = null;
    }
  }

  Future<void> _performCleanup() async {
    try {
      await native.disconnect();
    } catch (_) {
      error = 'native_disconnect_failed';
      phase = ConnectionPhase.failed;
      _emit();
      return;
    }
    try {
      if (_pendingBody != null)
        await _create(); // Same intent, even after a lost response.
      if (_tunnel != null) {
        final value = await api.call(
          _revokeSent ? 'GET' : 'DELETE',
          '/api/v1/tunnels/$_tunnel',
        );
        _revokeSent = value['state'] == 'revoke_pending';
        if (value['state'] == 'revoked') _tunnel = null;
        unawaited(refreshQuota());
      }
    } catch (_) {
      /* Retain intent/id until server removal is confirmed. */
    }
    remotePending = hasLease;
    if (remotePending) {
      error = 'revoke_unconfirmed';
      startPolling();
    }
  }

  Future<void> disconnect() async {
    if (_closing) return;
    _closing = true;
    ++_epoch;
    detail = 'Отключение VPN';
    _emit();
    try {
      // Do not wait for a slow HTTP request or permission dialog to stop VPN.
      await native.disconnect();
      await _connecting;
      await _cleanup();
      if (error != 'native_disconnect_failed') {
        phase = ConnectionPhase.disconnected;
        detail = 'VPN на телефоне выключен';
        if (!remotePending) error = null;
      }
    } catch (_) {
      phase = ConnectionPhase.failed;
      error = 'native_disconnect_failed';
    } finally {
      _closing = false;
      _emit();
    }
  }

  Future<void> refresh() async {
    if (_refreshing || busy || _disposed) return;
    _refreshing = true;
    final epoch = _epoch;
    try {
      if (remotePending) {
        await _cleanup();
        if (!remotePending && error == 'revoke_unconfirmed') error = null;
        return;
      }
      var snapshot = await native.snapshot();
      if (_cancelled(epoch)) return;
      if (_tunnel == null) {
        if (snapshot.up)
          await native.disconnect(); // No restore without API ownership.
        return;
      }
      if (_leaseClock!.elapsedMilliseconds >= _leaseLimitMs ||
          snapshot.reason == 'lease_expired') {
        error = 'lease_expired';
        phase = ConnectionPhase.failed;
        await _cleanup();
        return;
      }
      final current = await api.call('GET', '/api/v1/tunnels/$_tunnel');
      if (_cancelled(epoch)) return;
      if (current['state'] != 'active') {
        error = 'remote_revoked';
        phase = ConnectionPhase.failed;
        await _cleanup();
        return;
      }
      // A slow API response must not revive a snapshot taken before expiry/down.
      snapshot = await native.snapshot();
      if (_cancelled(epoch)) return;
      if (_leaseClock!.elapsedMilliseconds >= _leaseLimitMs ||
          snapshot.reason == 'lease_expired') {
        error = 'lease_expired';
        phase = ConnectionPhase.failed;
        await _cleanup();
        return;
      }
      if (!snapshot.up) {
        error = 'native_down';
        phase = ConnectionPhase.failed;
        await _cleanup();
        return;
      }
      error = null;
      phase = snapshot.confirmed
          ? ConnectionPhase.connected
          : ConnectionPhase.reconnecting;
      detail = snapshot.confirmed
          ? 'Свежий handshake подтверждён'
          : snapshot.network
          ? 'Ожидается handshake с узлом'
          : 'Нет доступной сети. Ожидается восстановление';
    } catch (failure) {
      if (_cancelled(epoch)) return;
      final code = failure is ClientError ? failure.code : 'native_unavailable';
      if (code == 'unauthorized' ||
          code == 'session_expired' ||
          code == 'device_revoked' ||
          code == 'not_found') {
        error = code;
        phase = ConnectionPhase.failed;
        await _cleanup();
      } else if (_tunnel != null) {
        phase = ConnectionPhase.reconnecting;
        error = code;
        detail = 'Связь с API не подтверждена; действует ограниченный lease';
      } else {
        phase = ConnectionPhase.failed;
        error = code;
      }
    } finally {
      _refreshing = false;
      _emit();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    _timer?.cancel();
    quota.removeListener(_emit);
    quota.dispose();
    super.dispose();
  }
}

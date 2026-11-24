import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'protected_api_transport.dart';
import 'session_store.dart';

class ClientError implements Exception {
  const ClientError(this.code);
  final String code;
  @override
  String toString() => 'ClientError'; // Never include remote data/credentials.
}

String newId() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

abstract interface class TunnelApi {
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]);
}

/// ADR-161: at most this many fallback API origins after the primary.
const maxFallbackOrigins = 2;

/// ADR-161: parses the build-time `VPN_API_FALLBACK_ORIGINS` value (comma-separated, empty = none).
/// Any malformed entry fails the whole configuration (the app then shows its setup screen).
List<Uri> parseFallbackOrigins(String value) {
  final parts = value
      .split(',')
      .map((part) => part.trim())
      .where((part) => part.isNotEmpty)
      .toList();
  if (parts.length > maxFallbackOrigins) {
    throw const ClientError('invalid_origin');
  }
  return [for (final part in parts) Uri.parse(part)];
}

/// ADR-195: host names of the fallback origins in [Api.origins] (the primary is first and is
/// excluded), lower-case, in order. The Windows runner routes these hosts around the tunnel.
List<String> fallbackApiHosts(List<Uri> origins) => [
  for (final origin in origins.skip(1)) origin.host.toLowerCase(),
];

/// No logging, redirects, disk cache, certificate override or release HTTP mode.
class Api implements TunnelApi {
  /// [fallbacks] (ADR-161): up to two more https origins of the same API, tried in order when
  /// the previous one fails at the connection level. Never a plain-HTTP origin.
  Api(
    this.origin, {
    HttpClient? client,
    this.store,
    List<Uri> fallbacks = const [],
    // Per attempt. Tests shorten it; production keeps 8 s.
    Duration requestTimeout = const Duration(seconds: 8),
  }) : _client = client ?? HttpClient(),
       _requestTimeout = requestTimeout,
       origins = List.unmodifiable([origin, ...fallbacks]) {
    final loopback =
        kDebugMode &&
        origin.scheme == 'http' &&
        origin.host == '127.0.0.1' &&
        origin.port == 18766;
    if ((!loopback && origin.scheme != 'https') || !_wellFormed(origin)) {
      throw const ClientError('invalid_origin');
    }
    if (fallbacks.length > maxFallbackOrigins) {
      throw const ClientError('invalid_origin');
    }
    for (var i = 0; i < fallbacks.length; i++) {
      final fallback = fallbacks[i];
      // ADR-161: TLS only, and each origin once (no duplicate of the primary or another one).
      if (fallback.scheme != 'https' ||
          !_wellFormed(fallback) ||
          origins.indexWhere((o) => _sameOrigin(o, fallback)) != i + 1) {
        throw const ClientError('invalid_origin');
      }
    }
    _client.connectionTimeout = const Duration(seconds: 5);
  }

  static bool _wellFormed(Uri value) =>
      value.host.isNotEmpty &&
      value.userInfo.isEmpty &&
      !value.hasQuery &&
      !value.hasFragment &&
      (value.path.isEmpty || value.path == '/');

  static bool _sameOrigin(Uri a, Uri b) =>
      a.scheme == b.scheme &&
      a.host.toLowerCase() == b.host.toLowerCase() &&
      a.port == b.port;

  /// ADR-161: the primary origin first, then the configured fallbacks. Nothing else is contacted.
  final List<Uri> origins;
  final Duration _requestTimeout;

  Api.protected(
    Uri origin, {
    required ProtectedRelayProvider relayProvider,
    SessionStore? store,
  }) : this(
         origin,
         client: protectedApiHttpClient(origin, relayProvider),
         store: store,
       );

  final Uri origin;
  final HttpClient _client;
  final SessionStore? store;
  Future<void> _storage = Future.value();
  Future<Map<String, dynamic>?> readPendingLogin() async {
    final pendingStore = store;
    if (pendingStore is PendingLoginStore) {
      return (pendingStore as PendingLoginStore).readPendingLogin();
    }
    return null;
  }

  Future<void> writePendingLogin(Map<String, dynamic>? value) async {
    final pendingStore = store;
    if (pendingStore is! PendingLoginStore) {
      if (value != null) throw const ClientError('login_storage_unavailable');
      return;
    }
    await (pendingStore as PendingLoginStore).writePendingLogin(value);
  }

  Future<void> _persist(Map<String, dynamic>? value) {
    final work = _storage.then((_) => store?.write(value));
    _storage = work.catchError((Object _) {});
    return work;
  }

  Future<void> restoreSession() async {
    final epoch = _sessionEpoch;
    Map<String, dynamic>? saved;
    try {
      saved = await store?.read();
    } catch (_) {
      // ADR-158: a failed read (for example a keystore error at a cold start) is not "no
      // session". The stored record is kept untouched for the next attempt.
      throw const ClientError('session_storage_unavailable');
    }
    if (epoch != _sessionEpoch || saved == null) return;
    if (saved['origin'] != origin.toString() ||
        saved['refresh_token'] is! String) {
      await _persist(null);
      return;
    }
    _refresh = saved['refresh_token'] as String;
    // Always validate/rotate with the server; never restore an old access token.
    final pending = _refreshing ??= _renew();
    try {
      await pending;
    } finally {
      if (identical(_refreshing, pending)) _refreshing = null;
    }
  }

  String? _access;
  String? _refresh;
  Future<void>? _refreshing;
  int _sessionEpoch = 0;
  bool get authenticated => _access != null;
  Future<void> acceptSession(Map<String, dynamic> value) async {
    final access = value['access_token'] as String;
    final refresh = value['refresh_token'] as String;
    ++_sessionEpoch;
    _access = access;
    _refresh = refresh;
    try {
      await _persist({'origin': origin.toString(), 'refresh_token': refresh});
    } catch (_) {
      await forgetSession();
      throw const ClientError('session_storage_failed');
    }
  }

  Future<void> forgetSession() {
    ++_sessionEpoch;
    _access = null;
    _refresh = null;
    return _persist(null);
  }

  void close() {
    ++_sessionEpoch;
    _access = null;
    _refresh = null;
    _client.close(force: true);
  }

  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    try {
      return await _request(method, path, body);
    } on ClientError catch (e) {
      if (e.code != 'unauthorized' ||
          (_refresh == null && _refreshing == null) ||
          path.startsWith('/api/v1/auth/'))
        rethrow;
      final pending = _refreshing ??= _renew();
      try {
        await pending;
      } finally {
        if (identical(_refreshing, pending)) _refreshing = null;
      }
      return _request(method, path, body);
    }
  }

  /// ADR-100: the old refresh token stays durable until the new pair is validated and stored.
  /// A lost response or transport failure keeps the session: the next attempt replays the
  /// same token, which the server accepts once within its grace window if the rotated
  /// successor was never used. Only an explicit refusal (401) or a storage failure ends it.
  Future<void> _renew() async {
    final token = _refresh;
    final epoch = _sessionEpoch;
    if (token == null) throw const ClientError('session_expired');
    Map<String, dynamic> value;
    try {
      value = await _request('POST', '/api/v1/auth/refresh', {
        'refresh_token': token,
      });
    } on ClientError catch (e) {
      if (epoch != _sessionEpoch) throw const ClientError('session_expired');
      // ADR-158: only the server's authoritative refusal (HTTP 401) ends the session. Any other
      // answer (a 5xx such as `auth_unavailable`, a malformed body) keeps the durable token.
      if (e.code != 'unauthorized') rethrow;
      await forgetSession();
      throw const ClientError('session_expired');
    }
    if (epoch != _sessionEpoch) throw const ClientError('session_expired');
    final access = value['access_token'];
    final refresh = value['refresh_token'];
    // A malformed 2xx answer is retryable: the durable token stays and the session is kept.
    if (access is! String || refresh is! String) {
      throw const ClientError('invalid_response');
    }
    try {
      await _persist({'origin': origin.toString(), 'refresh_token': refresh});
      if (epoch != _sessionEpoch) throw const ClientError('session_expired');
      _access = access;
      _refresh = refresh;
    } catch (_) {
      if (epoch == _sessionEpoch) await forgetSession();
      throw const ClientError('session_expired');
    }
  }

  Future<Map<String, dynamic>> _request(
    String method,
    String path,
    Map<String, dynamic>? body,
  ) async {
    if (!path.startsWith('/api/v1/') ||
        path.contains('..') ||
        path.contains('#') ||
        (path.contains('?') &&
            !(method == 'GET' &&
                (path == '/api/v1/window-tunnels/nodes?include=panel' ||
                    RegExp(
                      r'^/api/v1/quota/history\?limit=20&snapshot_revision=[0-9]{1,19}(&before_sequence=[0-9]{1,19})?$',
                    ).hasMatch(path))))) {
      throw const ClientError('invalid_request');
    }
    // ADR-161: a request the server may already have received is repeated on the next origin
    // only when repeating it is safe: an idempotent method or a body with an idempotency key.
    final key = body?['idempotency_key'];
    final repeatable =
        _idempotentMethods.contains(method) ||
        (key is String && key.isNotEmpty);
    for (var i = 0; ; i++) {
      try {
        return await _requestOnce(origins[i], method, path, body);
      } on _TransportFailure catch (failure) {
        final last = i + 1 >= origins.length;
        if (last || (failure.sent && !repeatable)) {
          throw const ClientError('network_unavailable');
        }
      }
    }
  }

  static const _idempotentMethods = {'GET', 'HEAD', 'PUT', 'DELETE', 'OPTIONS'};

  /// One attempt on [base]. An HTTP answer (any status, valid or not) is final and surfaces as
  /// a result or a [ClientError]; only a failure before any answer is a [_TransportFailure].
  Future<Map<String, dynamic>> _requestOnce(
    Uri base,
    String method,
    String path,
    Map<String, dynamic>? body,
  ) async {
    HttpClientRequest? request;
    var cancelled = false;
    var sent = false;
    var answered = false;
    try {
      return await (() async {
        request = await _client.openUrl(method, base.resolve(path));
        if (cancelled) {
          request!.abort();
          throw const _TransportFailure(sent: false);
        }
        request!.followRedirects = false;
        request!.headers.contentType = ContentType.json;
        request!.headers.set('Cache-Control', 'no-store');
        if (_access != null)
          request!.headers.set('Authorization', 'Bearer $_access');
        // From here bytes may leave the device (headers can be flushed with the body).
        sent = true;
        if (body != null) request!.write(jsonEncode(body));
        final response = await request!.close();
        answered = true;
        final bytes = <int>[];
        await for (final chunk in response) {
          bytes.addAll(chunk);
          if (bytes.length > 65536) {
            request!.abort();
            throw const ClientError('invalid_response');
          }
        }
        final Object? parsed = bytes.isEmpty
            ? <String, dynamic>{}
            : jsonDecode(utf8.decode(bytes));
        if (response.statusCode == 401) throw const ClientError('unauthorized');
        if (response.statusCode < 200 || response.statusCode >= 300) {
          final code =
              parsed is Map<String, dynamic> &&
                  parsed['error'] is Map<String, dynamic>
              ? (parsed['error'] as Map<String, dynamic>)['code']
              : null;
          throw ClientError(
            code is String && RegExp(r'^[a-z_]{1,64}$').hasMatch(code)
                ? code
                : 'api_unavailable',
          );
        }
        if (parsed is List &&
            method == 'GET' &&
            path == '/api/v1/window-tunnels') {
          return {'items': parsed};
        }
        if (parsed is! Map<String, dynamic>)
          throw const ClientError('invalid_response');
        return parsed;
      })().timeout(
        _requestTimeout,
        onTimeout: () {
          cancelled = true;
          request?.abort();
          // A timeout while reading an answer is an answered request: never repeated.
          if (answered) throw const ClientError('network_unavailable');
          throw _TransportFailure(sent: sent);
        },
      );
    } on ClientError {
      rethrow;
    } on _TransportFailure {
      rethrow;
    } catch (_) {
      request?.abort();
      if (answered) throw const ClientError('network_unavailable');
      throw _TransportFailure(sent: sent);
    }
  }
}

/// ADR-161: a failure before any HTTP answer. [sent] is true once the request may have reached
/// the server (after `close()` was called), so only a repeatable request is tried elsewhere.
final class _TransportFailure implements Exception {
  const _TransportFailure({required this.sent});
  final bool sent;
}

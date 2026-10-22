import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/foundation.dart';

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

/// No logging, redirects, disk cache, certificate override or release HTTP mode.
class Api implements TunnelApi {
  Api(this.origin, {HttpClient? client}) : _client = client ?? HttpClient() {
    final loopback =
        kDebugMode &&
        origin.scheme == 'http' &&
        origin.host == '127.0.0.1' &&
        origin.port == 18766;
    if ((!loopback && origin.scheme != 'https') ||
        origin.host.isEmpty ||
        origin.userInfo.isNotEmpty ||
        origin.hasQuery ||
        origin.hasFragment ||
        (origin.path.isNotEmpty && origin.path != '/')) {
      throw const ClientError('invalid_origin');
    }
    _client.connectionTimeout = const Duration(seconds: 5);
  }
  final Uri origin;
  final HttpClient _client;
  String? _access;
  String? _refresh;
  Future<void>? _refreshing;
  int _sessionEpoch = 0;
  bool get authenticated => _access != null;
  void acceptSession(Map<String, dynamic> value) {
    final access = value['access_token'] as String;
    final refresh = value['refresh_token'] as String;
    ++_sessionEpoch;
    _access = access;
    _refresh = refresh;
  }

  void forgetSession() {
    ++_sessionEpoch;
    _access = null;
    _refresh = null;
  }

  void close() {
    forgetSession();
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

  Future<void> _renew() async {
    final token = _refresh;
    final epoch = _sessionEpoch;
    // A lost response may have rotated the refresh token. Fail closed, require login.
    _refresh = null;
    try {
      final value = await _request('POST', '/api/v1/auth/refresh', {
        'refresh_token': token,
      });
      if (epoch != _sessionEpoch) throw const ClientError('session_expired');
      _access = value['access_token'] as String;
      _refresh = value['refresh_token'] as String;
    } catch (_) {
      if (epoch == _sessionEpoch) forgetSession();
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
                RegExp(
                  r'^/api/v1/quota/history\?limit=20&snapshot_revision=[0-9]{1,19}(&before_sequence=[0-9]{1,19})?$',
                ).hasMatch(path)))) {
      throw const ClientError('invalid_request');
    }
    HttpClientRequest? request;
    var cancelled = false;
    try {
      return await (() async {
        request = await _client.openUrl(method, origin.resolve(path));
        if (cancelled) {
          request!.abort();
          throw const ClientError('network_unavailable');
        }
        request!.followRedirects = false;
        request!.headers.contentType = ContentType.json;
        request!.headers.set('Cache-Control', 'no-store');
        if (_access != null)
          request!.headers.set('Authorization', 'Bearer $_access');
        if (body != null) request!.write(jsonEncode(body));
        final response = await request!.close();
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
        if (parsed is! Map<String, dynamic>)
          throw const ClientError('invalid_response');
        return parsed;
      })().timeout(
        const Duration(seconds: 8),
        onTimeout: () {
          cancelled = true;
          request?.abort();
          throw const ClientError('network_unavailable');
        },
      );
    } on ClientError {
      rethrow;
    } catch (_) {
      request?.abort();
      throw const ClientError('network_unavailable');
    }
  }
}

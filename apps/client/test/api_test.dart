import 'dart:io';
import 'dart:convert';
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/session_store.dart';

// TEST ONLY in-memory persistence, not an Android Keystore proof.
class MemoryStore implements SessionStore {
  Map<String, dynamic>? value;
  final writes = <Map<String, dynamic>?>[];
  bool fail = false;
  @override
  Future<Map<String, dynamic>?> read() async => value;
  @override
  Future<void> write(Map<String, dynamic>? record) async {
    if (fail) throw StateError('TEST ONLY storage error');
    value = record;
    writes.add(record);
  }
}

void main() {
  test(
    'storage failure clears RAM and never authenticates a saved session',
    () async {
      final store = MemoryStore()..fail = true;
      final api = Api(Uri.parse('https://example.invalid'), store: store);
      await expectLater(
        api.acceptSession({
          'access_token': 'TEST ONLY',
          'refresh_token': 'TEST ONLY',
        }),
        throwsA(anything),
      );
      expect(api.authenticated, false);
      api.close();
    },
  );
  test(
    'logout during startup rotation cannot restore durable credentials',
    () async {
      final store = MemoryStore()
        ..value = {
          'origin': 'http://127.0.0.1:18766',
          'refresh_token': 'TEST ONLY OLD',
        };
      final entered = Completer<void>();
      final release = Completer<void>();
      final server = await HttpServer.bind('127.0.0.1', 18766);
      server.listen((request) async {
        entered.complete();
        await release.future;
        request.response.write(
          jsonEncode({
            'access_token': 'TEST ONLY ACCESS',
            'refresh_token': 'TEST ONLY NEW',
          }),
        );
        await request.response.close();
      });
      final api = Api(Uri.parse('http://127.0.0.1:18766'), store: store);
      addTearDown(() async {
        api.close();
        await server.close(force: true);
      });
      final restoring = expectLater(
        api.restoreSession(),
        throwsA(isA<ClientError>()),
      );
      await entered.future;
      await api.forgetSession();
      release.complete();
      await restoring;
      expect(api.authenticated, false);
      expect(store.value, null);
    },
  );
  test(
    'persist only origin and refresh; close preserves and logout erases',
    () async {
      final store = MemoryStore();
      final api = Api(Uri.parse('https://example.invalid'), store: store);
      await api.acceptSession({
        'access_token': 'TEST ONLY ACCESS',
        'refresh_token': 'TEST ONLY REFRESH',
      });
      expect(store.value!.keys.toSet(), {'origin', 'refresh_token'});
      api.close();
      expect(store.value, isNotNull);
      await api.forgetSession();
      expect(store.value, null);
    },
  );
  test('origin mismatch erases without sending a refresh request', () async {
    final store = MemoryStore()
      ..value = {
        'origin': 'https://other.invalid',
        'refresh_token': 'TEST ONLY',
      };
    final api = Api(Uri.parse('https://example.invalid'), store: store);
    await api.restoreSession();
    expect(api.authenticated, false);
    expect(store.value, null);
    api.close();
  });
  test(
    'refresh is consumed durably before request and replaced after validation',
    () async {
      final store = MemoryStore()
        ..value = {
          'origin': 'http://127.0.0.1:18766',
          'refresh_token': 'TEST ONLY OLD',
        };
      final server = await HttpServer.bind('127.0.0.1', 18766);
      var consumed = false;
      server.listen((request) async {
        consumed = store.value == null;
        request.response.write(
          jsonEncode({
            'access_token': 'TEST ONLY ACCESS',
            'refresh_token': 'TEST ONLY NEW',
          }),
        );
        await request.response.close();
      });
      final api = Api(Uri.parse('http://127.0.0.1:18766'), store: store);
      addTearDown(() async {
        api.close();
        await server.close(force: true);
      });
      await api.restoreSession();
      expect(consumed, true);
      expect(api.authenticated, true);
      expect(store.value!['refresh_token'], 'TEST ONLY NEW');
    },
  );
  test(
    'lost response leaves no durable token to replay on next startup',
    () async {
      final store = MemoryStore()
        ..value = {
          'origin': 'http://127.0.0.1:18766',
          'refresh_token': 'TEST ONLY OLD',
        };
      final server = await HttpServer.bind('127.0.0.1', 18766);
      server.listen((request) async {
        request.response.statusCode = 503;
        await request.response.close();
      });
      final api = Api(Uri.parse('http://127.0.0.1:18766'), store: store);
      addTearDown(() async {
        api.close();
        await server.close(force: true);
      });
      await expectLater(api.restoreSession(), throwsA(isA<ClientError>()));
      expect(store.value, null);
      expect(api.authenticated, false);
    },
  );

  test(
    'history cursor query is sent intact; other queries are rejected',
    () async {
      final server = await HttpServer.bind('127.0.0.1', 18766);
      Uri? received;
      server.listen((request) async {
        received = request.uri;
        request.response.write('{}');
        await request.response.close();
      });
      final api = Api(Uri.parse('http://127.0.0.1:18766'));
      addTearDown(() async {
        api.close();
        await server.close(force: true);
      });
      await api.call(
        'GET',
        '/api/v1/quota/history?limit=20&snapshot_revision=9007199254740993&before_sequence=21',
      );
      expect(
        received!.queryParameters['snapshot_revision'],
        '9007199254740993',
      );
      expect(received!.queryParameters['before_sequence'], '21');
      for (final path in [
        '/api/v1/quota?user_id=other',
        '/api/v1/quota/history?limit=20&snapshot_revision=1&user_id=other',
        '/api/v1/quota/history?limit=20&snapshot_revision=1#secret',
        '/api/v1/quota/history?limit=20&snapshot_revision=-1',
      ]) {
        await expectLater(api.call('GET', path), throwsA(isA<ClientError>()));
      }
      await expectLater(
        api.call('POST', '/api/v1/quota/history?limit=20&snapshot_revision=1'),
        throwsA(isA<ClientError>()),
      );
    },
  );
  test(
    'rejects plaintext remote origins, credentials, paths and fragments',
    () {
      for (final value in [
        'http://example.com',
        'http://localhost:18766',
        'http://127.0.0.1:8080',
        'https://user:pass@example.com',
        'https://example.com/path',
        'https://example.com#x',
      ]) {
        expect(() => Api(Uri.parse(value)), throwsA(isA<ClientError>()));
      }
      final api = Api(Uri.parse('https://example.com'));
      api.close();
    },
  );
  test('random idempotency keys are canonical UUID v4 and independent', () {
    final values = List.generate(100, (_) => newId());
    expect(values.toSet().length, 100);
    for (final id in values) {
      expect(
        id,
        matches(
          r'^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$',
        ),
      );
    }
  });
  test('HTTP redirects cannot forward a session to another host', () async {
    final server = await HttpServer.bind('127.0.0.1', 18766);
    var requests = 0;
    server.listen((request) async {
      requests++;
      request.response.statusCode = 302;
      request.response.headers.set(
        'location',
        'https://example.invalid/stolen',
      );
      await request.response.close();
    });
    final api = Api(Uri.parse('http://127.0.0.1:18766'));
    addTearDown(() async {
      api.close();
      await server.close(force: true);
    });
    await expectLater(
      api.call('GET', '/api/v1/me'),
      throwsA(isA<ClientError>()),
    );
    expect(requests, 1);
  });
  test('concurrent unauthorized requests rotate refresh once', () async {
    final server = await HttpServer.bind('127.0.0.1', 18766);
    var rotations = 0;
    server.listen((request) async {
      request.response.headers.contentType = ContentType.json;
      if (request.uri.path.endsWith('/refresh')) {
        rotations++;
        await Future<void>.delayed(const Duration(milliseconds: 40));
        request.response.write(
          jsonEncode({
            'access_token': 'TEST-ONLY-NEW',
            'refresh_token': 'TEST-ONLY-ROTATED',
          }),
        );
      } else if (request.headers.value('Authorization') ==
          'Bearer TEST-ONLY-NEW') {
        request.response.write('{}');
      } else {
        request.response.statusCode = 401;
        request.response.write('{"error":{"code":"unauthorized"}}');
      }
      await request.response.close();
    });
    final api = Api(Uri.parse('http://127.0.0.1:18766'));
    await api.acceptSession({
      'access_token': 'TEST-ONLY-OLD',
      'refresh_token': 'TEST-ONLY-REFRESH',
    });
    addTearDown(() async {
      api.close();
      await server.close(force: true);
    });
    await Future.wait([
      api.call('GET', '/api/v1/me'),
      api.call('GET', '/api/v1/quota'),
    ]);
    expect(rotations, 1);
  });
  test('logout during refresh cannot restore erased session', () async {
    final entered = Completer<void>();
    final release = Completer<void>();
    final server = await HttpServer.bind('127.0.0.1', 18766);
    server.listen((request) async {
      request.response.headers.contentType = ContentType.json;
      if (request.uri.path.endsWith('/refresh')) {
        entered.complete();
        await release.future;
        request.response.write(
          jsonEncode({
            'access_token': 'TEST-ONLY-NEW',
            'refresh_token': 'TEST-ONLY-ROTATED',
          }),
        );
      } else {
        request.response.statusCode = 401;
        request.response.write('{}');
      }
      await request.response.close();
    });
    final api = Api(Uri.parse('http://127.0.0.1:18766'));
    await api.acceptSession({
      'access_token': 'TEST-ONLY-OLD',
      'refresh_token': 'TEST-ONLY-REFRESH',
    });
    addTearDown(() async {
      api.close();
      await server.close(force: true);
    });
    final pending = api.call('GET', '/api/v1/me');
    final check = expectLater(pending, throwsA(isA<ClientError>()));
    await entered.future;
    await api.forgetSession();
    release.complete();
    await check;
    expect(api.authenticated, false);
  });
}

import 'dart:io';
import 'dart:convert';
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';

void main() {
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
    api.acceptSession({
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
    api.acceptSession({
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
    api.forgetSession();
    release.complete();
    await check;
    expect(api.authenticated, false);
  });
}

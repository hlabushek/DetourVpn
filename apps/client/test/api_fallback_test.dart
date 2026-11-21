import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';

// TEST ONLY transport fakes (ADR-161). No socket is opened and no real host is contacted; the
// fake records every URL the Api asks for, which is what the "only configured hosts" rule checks.

enum Behaviour {
  /// Connection refused / DNS / TLS failure in `openUrl`: nothing was sent.
  refuse,

  /// The request was sent, then the connection broke before any answer.
  dropAfterSend,

  /// The request was sent and no answer came (per-attempt timeout).
  hangAfterSend,

  /// An HTTP answer.
  answer,
}

class Host {
  Host(this.behaviour, {this.status = 200, this.body = '{"ok":true}'});
  final Behaviour behaviour;
  final int status;
  final String body;
  final Map<String, String> headers = {};
}

class FakeClient implements HttpClient {
  FakeClient(this.hosts);
  final Map<String, Host> hosts;
  final contacted = <Uri>[];
  final authorizations = <String, String?>{};

  @override
  Duration? connectionTimeout;

  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    contacted.add(url);
    final host = hosts[url.host];
    if (host == null || host.behaviour == Behaviour.refuse) {
      throw const SocketException('TEST ONLY connection refused');
    }
    return FakeRequest(this, url, host);
  }

  @override
  void close({bool force = false}) {}

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeHeaders implements HttpHeaders {
  final values = <String, String>{};
  @override
  ContentType? contentType;
  @override
  void set(String name, Object value, {bool preserveHeaderCase = false}) =>
      values[name.toLowerCase()] = '$value';
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeRequest implements HttpClientRequest {
  FakeRequest(this.client, this.url, this.host);
  final FakeClient client;
  final Uri url;
  final Host host;
  @override
  bool followRedirects = true;
  @override
  final FakeHeaders headers = FakeHeaders();

  @override
  void write(Object? object) {}

  @override
  void abort([Object? exception, StackTrace? stackTrace]) {}

  @override
  Future<HttpClientResponse> close() async {
    client.authorizations[url.host] = headers.values['authorization'];
    switch (host.behaviour) {
      case Behaviour.dropAfterSend:
        throw const SocketException('TEST ONLY reset after send');
      case Behaviour.hangAfterSend:
        return Completer<HttpClientResponse>().future;
      case Behaviour.answer:
      case Behaviour.refuse:
        return FakeResponse(host.status, utf8.encode(host.body));
    }
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class FakeResponse extends Stream<List<int>> implements HttpClientResponse {
  FakeResponse(this.statusCode, this.bytes);
  @override
  final int statusCode;
  final List<int> bytes;

  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int>)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) => Stream<List<int>>.fromIterable([bytes]).listen(
    onData,
    onError: onError,
    onDone: onDone,
    cancelOnError: cancelOnError,
  );

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

final primary = Uri.parse('https://primary.example.invalid');
final fallbackA = Uri.parse('https://fallback-a.example.invalid');
final fallbackB = Uri.parse('https://fallback-b.example.invalid');

Api apiWith(FakeClient client, {List<Uri>? fallbacks}) => Api(
  primary,
  client: client,
  fallbacks: fallbacks ?? [fallbackA, fallbackB],
  requestTimeout: const Duration(milliseconds: 200),
);

void main() {
  test('a refused primary falls back in the configured order', () async {
    final client = FakeClient({
      primary.host: Host(Behaviour.refuse),
      fallbackA.host: Host(Behaviour.refuse),
      fallbackB.host: Host(Behaviour.answer),
    });
    final api = apiWith(client);
    expect(await api.call('GET', '/api/v1/me'), {'ok': true});
    expect(client.contacted.map((u) => u.host), [
      primary.host,
      fallbackA.host,
      fallbackB.host,
    ]);
    // The configured path is kept and TLS (https) is used on every origin.
    expect(client.contacted.every((u) => u.scheme == 'https'), true);
    expect(client.contacted.every((u) => u.path == '/api/v1/me'), true);
  });

  test('a healthy primary is the only host contacted', () async {
    final client = FakeClient({primary.host: Host(Behaviour.answer)});
    final api = apiWith(client);
    await api.call('GET', '/api/v1/me');
    await api.call('POST', '/api/v1/auth/start');
    expect(client.contacted.map((u) => u.host).toSet(), {primary.host});
  });

  for (final status in [400, 401, 404, 409, 422, 500, 502, 503]) {
    test('an HTTP $status answer is never retried on another origin', () async {
      final client = FakeClient({
        primary.host: Host(
          Behaviour.answer,
          status: status,
          body: '{"error":{"code":"test_only"}}',
        ),
        fallbackA.host: Host(Behaviour.answer),
      });
      final api = apiWith(client);
      await expectLater(
        api.call('GET', '/api/v1/me'),
        throwsA(isA<ClientError>()),
      );
      expect(client.contacted.map((u) => u.host), [primary.host]);
    });
  }

  test(
    'a redirect is an answer: no retry and its target is never contacted',
    () async {
      final client = FakeClient({
        primary.host: Host(Behaviour.answer, status: 302, body: ''),
        fallbackA.host: Host(Behaviour.answer),
      });
      final api = apiWith(client);
      await expectLater(
        api.call('GET', '/api/v1/me'),
        throwsA(isA<ClientError>()),
      );
      expect(client.contacted.map((u) => u.host), [primary.host]);
    },
  );

  test(
    'a non-idempotent POST that may have been delivered is not repeated',
    () async {
      for (final behaviour in [
        Behaviour.dropAfterSend,
        Behaviour.hangAfterSend,
      ]) {
        final client = FakeClient({
          primary.host: Host(behaviour),
          fallbackA.host: Host(Behaviour.answer),
        });
        final api = apiWith(client);
        await expectLater(
          api.call('POST', '/api/v1/auth/refresh', {
            'refresh_token': 'TEST ONLY',
          }),
          throwsA(
            isA<ClientError>().having(
              (e) => e.code,
              'code',
              'network_unavailable',
            ),
          ),
        );
        expect(client.contacted.map((u) => u.host), [primary.host]);
      }
    },
  );

  test('a non-idempotent POST that was never sent does fall back', () async {
    final client = FakeClient({
      primary.host: Host(Behaviour.refuse),
      fallbackA.host: Host(Behaviour.answer),
    });
    final api = apiWith(client);
    await api.call('POST', '/api/v1/auth/refresh', {
      'refresh_token': 'TEST ONLY',
    });
    expect(client.contacted.map((u) => u.host), [primary.host, fallbackA.host]);
  });

  test(
    'a POST with an idempotency key and a GET are repeated after a timeout',
    () async {
      final client = FakeClient({
        primary.host: Host(Behaviour.hangAfterSend),
        fallbackA.host: Host(Behaviour.dropAfterSend),
        fallbackB.host: Host(Behaviour.answer),
      });
      final api = apiWith(client);
      await api.call('POST', '/api/v1/tunnels', {
        'idempotency_key': newId(),
        'node_id': null,
      });
      expect(client.contacted.map((u) => u.host), [
        primary.host,
        fallbackA.host,
        fallbackB.host,
      ]);
      client.contacted.clear();
      await api.call('GET', '/api/v1/quota');
      expect(client.contacted.length, 3);
    },
  );

  test('every origin failing reports network_unavailable', () async {
    final client = FakeClient({});
    final api = apiWith(client);
    await expectLater(
      api.call('GET', '/api/v1/me'),
      throwsA(
        isA<ClientError>().having((e) => e.code, 'code', 'network_unavailable'),
      ),
    );
    expect(client.contacted.length, 3);
  });

  test('session credentials reach only configured origins', () async {
    final client = FakeClient({
      primary.host: Host(Behaviour.refuse),
      fallbackA.host: Host(Behaviour.answer, body: '{}'),
    });
    final api = apiWith(client);
    await api.acceptSession({
      'access_token': 'TEST-ONLY-ACCESS',
      'refresh_token': 'TEST-ONLY-REFRESH',
    });
    await api.call('GET', '/api/v1/me');
    expect(client.authorizations.keys.toSet(), {fallbackA.host});
    expect(client.authorizations[fallbackA.host], 'Bearer TEST-ONLY-ACCESS');
    final configured = {primary.host, fallbackA.host, fallbackB.host};
    expect(client.contacted.every((u) => configured.contains(u.host)), true);
  });

  test('without fallbacks the behaviour is the single-origin one', () async {
    final client = FakeClient({});
    final api = apiWith(client, fallbacks: const []);
    await expectLater(
      api.call('GET', '/api/v1/me'),
      throwsA(isA<ClientError>()),
    );
    expect(client.contacted.map((u) => u.host), [primary.host]);
  });

  test(
    'fallback configuration is validated: https only, at most two, no duplicates',
    () {
      for (final bad in [
        [Uri.parse('http://fallback-a.example.invalid')],
        [Uri.parse('http://127.0.0.1:18766')],
        [Uri.parse('https://user:pass@fallback-a.example.invalid')],
        [Uri.parse('https://fallback-a.example.invalid/path')],
        [Uri.parse('https://fallback-a.example.invalid?x=1')],
        [Uri.parse('https://PRIMARY.example.invalid')],
        [fallbackA, fallbackA],
        [fallbackA, fallbackB, Uri.parse('https://fallback-c.example.invalid')],
      ]) {
        expect(
          () => Api(primary, client: FakeClient({}), fallbacks: bad),
          throwsA(isA<ClientError>()),
          reason: '$bad',
        );
      }
      expect(parseFallbackOrigins(''), isEmpty);
      expect(
        parseFallbackOrigins(
          ' https://a.example.invalid , https://b.example.invalid ',
        ),
        [
          Uri.parse('https://a.example.invalid'),
          Uri.parse('https://b.example.invalid'),
        ],
      );
      expect(
        () => parseFallbackOrigins(
          'https://a.example.invalid,https://b.example.invalid,https://c.example.invalid',
        ),
        throwsA(isA<ClientError>()),
      );
    },
  );
}

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/always_on_recovery.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/session_store.dart';

const _origin = 'https://api.example.test';
const _node = '11111111-1111-4111-8111-111111111111';
const _device = '22222222-2222-4222-8222-222222222222';
const _oldConnection = '33333333-3333-4333-8333-333333333333';

class _MemorySessionStore implements SessionStore {
  @override
  Future<Map<String, dynamic>?> read() async => {
    'origin': _origin,
    'refresh_token': 'TEST ONLY synthetic refresh token',
  };

  @override
  Future<void> write(Map<String, dynamic>? value) async {}
}

class _RecoveryApi extends Api {
  _RecoveryApi({
    required this.availableNode,
    this.failRestore = false,
    this.existingWindows = const [],
    List<String>? timeline,
  }) : timeline = timeline ?? [],
       super(Uri.parse(_origin));

  final bool availableNode;
  final bool failRestore;
  final List<Map<String, dynamic>> existingWindows;
  final List<String> timeline;
  final calls = <(String, String)>[];
  bool isAuthenticated = false;
  bool closed = false;

  @override
  Future<void> restoreSession() async {
    if (failRestore) throw const ClientError('session_expired');
    isAuthenticated = true;
  }

  @override
  bool get authenticated => isAuthenticated;

  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    calls.add((method, path));
    timeline.add('api:$method $path');
    if (method == 'GET' && path == '/api/v1/window-tunnels/nodes') {
      return {
        'checked_at': '2026-10-29T12:00:00Z',
        'nodes': [
          {
            'id': _node,
            'name': 'TEST ONLY node',
            'region': 'test',
            'available': availableNode,
          },
        ],
      };
    }
    if (method == 'POST' && path == '/api/v1/devices') {
      return {'id': _device};
    }
    if (method == 'GET' && path == '/api/v1/window-tunnels') {
      return {'items': existingWindows};
    }
    if (method == 'DELETE' &&
        path == '/api/v1/window-tunnels/$_oldConnection') {
      return {'state': 'closed'};
    }
    return <String, dynamic>{};
  }

  @override
  void close() {
    closed = true;
    super.close();
  }
}

class _RecoveryNative implements WindowNative {
  _RecoveryNative({this.allowed = true, List<String>? timeline})
    : timeline = timeline ?? [];

  final bool allowed;
  final List<String> timeline;
  final calls = <String>[];
  int windowStarts = 0;

  @override
  Future<bool> permission() async {
    calls.add('permission');
    timeline.add('native:permission');
    return allowed;
  }

  @override
  Future<String> publicKey() async {
    calls.add('publicKey');
    timeline.add('native:publicKey');
    return 'TEST ONLY public key';
  }

  @override
  Future<String> prove(String ephemeralKey, String transcript) async =>
      'TEST ONLY proof';

  @override
  Future<void> connect(String config, int remainingMs) async {}

  @override
  Future<void> connectWindow(
    String config,
    Map<String, dynamic> grant,
    int remainingMs,
  ) async {
    windowStarts++;
    calls.add('connectWindow');
    timeline.add('native:connectWindow');
  }

  @override
  Future<void> advanceWindow(
    Map<String, dynamic> grant,
    int remainingMs,
  ) async {}

  @override
  Future<void> disconnect() async {
    calls.add('disconnect');
    timeline.add('native:disconnect');
  }

  @override
  Future<NativeSnapshot> snapshot() async => const NativeSnapshot(
    up: false,
    network: false,
    handshakeAge: -1,
    remainingMs: 0,
  );

  @override
  Future<void> openLogin(String url) async {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('project.vpn/tunnel.v1');
  final platformCalls = <String>[];

  setUp(() {
    platformCalls.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          platformCalls.add(call.method);
          return null;
        });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  Future<void> runRecovery({
    required _RecoveryApi api,
    required _RecoveryNative native,
    String? selectedNode = _node,
  }) => runAlwaysOnRecoveryWithDependencies(
    store: _MemorySessionStore(),
    native: native,
    readSelectedNode: () async => selectedNode,
    createApi: (origin, store) => api,
  );

  test(
    'missing encrypted session keeps the tunnel down and rejects recovery',
    () async {
      final store = _EmptySessionStore();
      final native = _RecoveryNative();
      _RecoveryApi? createdApi;

      await runAlwaysOnRecoveryWithDependencies(
        store: store,
        native: native,
        readSelectedNode: () async => _node,
        createApi: (origin, sessionStore) {
          final api = _RecoveryApi(availableNode: true);
          createdApi = api;
          return api;
        },
      );

      expect(createdApi, isNull);
      expect(native.calls, contains('disconnect'));
      expect(native.windowStarts, 0);
      expect(platformCalls, contains('bootstrapFailed'));
      expect(platformCalls, isNot(contains('bootstrapComplete')));
    },
  );

  test(
    'expired refreshed session never reads relay or creates a lease',
    () async {
      final api = _RecoveryApi(availableNode: true, failRestore: true);
      final native = _RecoveryNative();

      await runRecovery(api: api, native: native);

      expect(api.calls, isEmpty);
      expect(api.closed, isTrue);
      expect(native.calls, ['disconnect']);
      expect(native.windowStarts, 0);
      expect(platformCalls, contains('bootstrapFailed'));
      expect(platformCalls, isNot(contains('bootstrapComplete')));
    },
  );

  test(
    'non-HTTPS saved origin never requests a protected relay or a lease',
    () async {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            platformCalls.add(call.method);
            if (call.method == 'sessionRead') {
              return <String, Object?>{
                'origin': 'http://api.example.test',
                'refresh_token': 'TEST ONLY synthetic token',
              };
            }
            return null;
          });

      await runAlwaysOnRecovery();

      expect(platformCalls, contains('disconnect'));
      expect(platformCalls, contains('bootstrapFailed'));
      expect(platformCalls, isNot(contains('protectedRelayRead')));
      expect(platformCalls, isNot(contains('connectWindow')));
    },
  );

  test(
    'unavailable saved node rejects recovery before device registration',
    () async {
      final api = _RecoveryApi(availableNode: false);
      final native = _RecoveryNative();

      await runRecovery(api: api, native: native);

      expect(api.calls, [('GET', '/api/v1/window-tunnels/nodes')]);
      expect(native.calls, ['disconnect']);
      expect(native.windowStarts, 0);
      expect(platformCalls, contains('bootstrapFailed'));
    },
  );

  test(
    'old remote lease is closed before permission denial, never reused',
    () async {
      final timeline = <String>[];
      final api = _RecoveryApi(
        availableNode: true,
        timeline: timeline,
        existingWindows: [
          {'id': _oldConnection, 'state': 'active'},
        ],
      );
      final native = _RecoveryNative(allowed: false, timeline: timeline);

      await runRecovery(api: api, native: native);

      expect(
        api.calls,
        contains(('DELETE', '/api/v1/window-tunnels/$_oldConnection')),
      );
      expect(
        timeline.indexOf('api:DELETE /api/v1/window-tunnels/$_oldConnection'),
        lessThan(timeline.indexOf('native:permission')),
      );
      expect(api.calls, isNot(contains(('POST', '/api/v1/window-tunnels'))));
      expect(
        api.calls.where((call) => call.$2.contains('/key-challenge/')),
        isEmpty,
      );
      expect(native.calls, contains('permission'));
      expect(native.windowStarts, 0);
      expect(platformCalls, contains('bootstrapFailed'));
      expect(platformCalls, isNot(contains('bootstrapComplete')));
    },
  );
}

class _EmptySessionStore implements SessionStore {
  @override
  Future<Map<String, dynamic>?> read() async => null;

  @override
  Future<void> write(Map<String, dynamic>? value) async {}
}

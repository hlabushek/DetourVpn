import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/native_tunnel.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('project.vpn/tunnel.v1');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  const selectedNode = '11111111-1111-4111-8111-111111111111';

  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  test(
    'node preference uses the native private store and validates ids',
    () async {
      final calls = <MethodCall>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call);
        if (call.method == 'nodeRead') return selectedNode;
        return null;
      });

      final store = PlatformTunnel();
      expect(await store.readSelectedNode(), selectedNode);
      await store.writeSelectedNode(selectedNode);
      await store.writeSelectedNode(null);

      expect(calls.map((call) => call.method), [
        'nodeRead',
        'nodeWrite',
        'nodeWrite',
      ]);
      expect(calls[1].arguments, {'id': selectedNode});
      expect(calls[2].arguments, {'id': null});
      await expectLater(
        store.writeSelectedNode('../invalid'),
        throwsArgumentError,
      );
      expect(calls, hasLength(3));
    },
  );
  test('explicit intent is separate from low-level tunnel teardown', () async {
    final calls = <MethodCall>[];
    messenger.setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      return null;
    });
    final native = PlatformTunnel();
    await native.setDesiredConnected(true);
    await native.disconnect();
    await native.setDesiredConnected(false);
    expect(calls.map((c) => c.method), [
      'sessionIntent',
      'disconnect',
      'sessionIntent',
    ]);
    expect(calls.first.arguments, {'desired': true});
    expect(calls.last.arguments, {'desired': false});
  });
  test(
    'native negative health never falls back to handshake success',
    () async {
      messenger.setMockMethodCallHandler(
        channel,
        (call) async => {
          'up': true,
          'network': true,
          'handshakeAge': 0,
          'remainingMs': 60000,
          'reason': '',
          'healthConfirmed': false,
          'connectionState': 'DEGRADED',
          'desiredConnected': true,
        },
      );
      final snapshot = await PlatformTunnel().snapshot();
      expect(snapshot.confirmed, false);
      expect(snapshot.desiredConnected, true);
      expect(snapshot.connectionState, 'DEGRADED');
    },
  );
}

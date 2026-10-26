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
}

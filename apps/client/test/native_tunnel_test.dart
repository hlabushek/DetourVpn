import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
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

  test(
    'ADR-195: fallback API hosts go with every connect only when configured',
    () async {
      final calls = <MethodCall>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call);
        return null;
      });
      final grant = {'connection_id': 'c', 'runtime_id': 'r', 'generation': 1};

      // No fallbacks (Android, or Windows without them): arguments exactly as before.
      final plain = PlatformTunnel();
      await plain.connect('cfg', 1000);
      await plain.connectWindow('cfg', grant, 1000);
      await plain.connectVless({'server': 's'}, 1000);
      for (final call in calls) {
        expect(
          (call.arguments as Map).containsKey('apiFallbackHosts'),
          isFalse,
        );
      }

      calls.clear();
      final windows = PlatformTunnel(
        apiFallbackHosts: fallbackApiHosts([
          Uri.parse('https://api.example.test'),
          Uri.parse('https://API2.Example.test:8443'),
          Uri.parse('https://198.51.100.20'),
        ]),
      );
      await windows.connect('cfg', 1000);
      await windows.connectWindow('cfg', grant, 1000);
      await windows.connectVless({'server': 's'}, 1000);
      expect(calls.map((c) => c.method), [
        'connect',
        'connectWindow',
        'connectVless',
      ]);
      for (final call in calls) {
        expect((call.arguments as Map)['apiFallbackHosts'], [
          'api2.example.test',
          '198.51.100.20',
        ]);
      }
      expect((calls[1].arguments as Map)['config'], 'cfg');
      expect((calls[2].arguments as Map)['maxMs'], 1000);
    },
  );
}

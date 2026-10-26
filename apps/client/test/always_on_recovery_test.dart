import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/always_on_recovery.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('project.vpn/tunnel.v1');

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test(
    'missing encrypted session keeps the tunnel down and rejects recovery',
    () async {
      final calls = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            calls.add(call.method);
            return null;
          });

      await runAlwaysOnRecovery();

      expect(calls, contains('sessionRead'));
      expect(calls, contains('disconnect'));
      expect(calls, contains('bootstrapFailed'));
      expect(calls, isNot(contains('protectedRelayRead')));
      expect(calls, isNot(contains('connectWindow')));
    },
  );

  test(
    'non-HTTPS saved origin never requests a protected relay or a lease',
    () async {
      final calls = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            calls.add(call.method);
            if (call.method == 'sessionRead') {
              return <String, Object?>{
                'origin': 'http://api.example.test',
                'refresh_token': 'TEST ONLY synthetic token',
              };
            }
            return null;
          });

      await runAlwaysOnRecovery();

      expect(calls, contains('disconnect'));
      expect(calls, contains('bootstrapFailed'));
      expect(calls, isNot(contains('protectedRelayRead')));
      expect(calls, isNot(contains('connectWindow')));
    },
  );
}

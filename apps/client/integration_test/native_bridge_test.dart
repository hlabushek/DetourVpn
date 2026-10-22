import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:vpn_client/main.dart';
import 'package:vpn_client/native_tunnel.dart';

// Real device + real upstream core; no external endpoint or traffic is started.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
    'native core/key/channel and permission cancellation on physical Android',
    (tester) async {
      final native = PlatformTunnel();
      await tester.pumpWidget(VpnApp(api: null, native: native));
      final publicKey = await native.publicKey();
      expect(base64Decode(publicKey).length, 32);
      final before = await native.snapshot();
      expect(before.up, false);
      expect(before.remainingMs, 0);
      await expectLater(
        native.prove('invalid', 'invalid'),
        throwsA(isA<PlatformException>()),
      );
      await expectLater(
        native.connect('[Interface]\nPrivateKey = invalid', 1000),
        throwsA(isA<PlatformException>()),
      );
      await expectLater(
        native.connect('', 60001),
        throwsA(isA<PlatformException>()),
      );
      final permission = native.permission();
      await Future<void>.delayed(const Duration(milliseconds: 500));
      await native.disconnect();
      // Fresh app must not already have Android VPN approval.
      expect(await permission, false);
      final after = await native.snapshot();
      expect(after.up, false);
      expect(after.remainingMs, 0);
    },
  );
}

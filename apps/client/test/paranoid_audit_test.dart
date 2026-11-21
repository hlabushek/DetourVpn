import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/window_connection.dart';

import 'window_connection_test.dart' show Native, WindowsApi, node;

// TEST ONLY simulated API/native for the paranoid-audit fixes (ADR-164 onwards).

void main() {
  late WindowsApi api;
  late Native native;
  WindowConnectionController controller() {
    final value = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: null,
    )..selectedNode = node;
    addTearDown(value.dispose);
    return value;
  }

  setUp(() {
    api = WindowsApi();
    native = Native()..age = 0;
  });

  group('ADR-164: a failed native stop inside a cleanup', () {
    test('never escapes a poll and revokes nothing', () async {
      final c = controller();
      await c.connect();
      native
        ..up = false
        ..failStop = true;
      await expectLater(c.refresh(), completes);
      expect(c.phase, ConnectionPhase.failed);
      expect(c.error, 'native_disconnect_failed');
      expect(c.diagnosticStage, 'native_disconnect');
      expect(api.deletes, 0, reason: 'no revoke while the stop is unconfirmed');
      expect(c.hasLease, true);
      // The next poll retries the stop and finishes the cleanup.
      native.failStop = false;
      await c.refresh();
      expect(api.deletes, 1);
    });

    test('never escapes a failed connect', () async {
      final c = controller();
      api.denied = false;
      native
        ..failStop = true
        ..failPublicKey = true;
      await expectLater(c.connect(), completes);
      expect(c.phase, ConnectionPhase.failed);
      expect(c.error, 'native_disconnect_failed');
    });
  });
}

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/window_connection.dart';

import 'window_connection_test.dart' show Native, WindowsApi, node;

// TEST ONLY simulated API/native for the paranoid-audit fixes (ADR-164 onwards).

/// TEST ONLY native whose channel calls can be held unanswered (ADR-185). A held call answers
/// only when the test completes its gate; a held start brings the tunnel up when it answers,
/// as a late native start would.
class HangingNative extends Native {
  Completer<void>? holdSnapshot;
  Completer<void>? holdStart;
  Completer<void>? holdStop;
  int startsAnswered = 0;

  @override
  Future<NativeSnapshot> snapshot() async {
    final gate = holdSnapshot;
    if (gate != null) await gate.future;
    return super.snapshot();
  }

  @override
  Future<void> connectWindow(
    String config,
    Map<String, dynamic> grant,
    int remainingMs,
  ) async {
    final gate = holdStart;
    if (gate != null) await gate.future;
    await super.connectWindow(config, grant, remainingMs);
    startsAnswered++;
  }

  @override
  Future<void> disconnect() async {
    final gate = holdStop;
    if (gate != null) await gate.future;
    return super.disconnect();
  }
}

void main() {
  late WindowsApi api;
  late HangingNative native;
  WindowConnectionController controller() {
    final value = WindowConnectionController(
      api,
      native,
      pollInterval: const Duration(days: 1),
      renewBeforeMs: 90000,
      autoReconnectDelay: null,
      // ADR-185: short bounds so the timeout paths run quickly in tests.
      nativeCallTimeout: const Duration(milliseconds: 150),
      nativeStartTimeout: const Duration(milliseconds: 150),
      snapshotRetryDelay: const Duration(milliseconds: 1),
    )..selectedNode = node;
    addTearDown(value.dispose);
    return value;
  }

  setUp(() {
    api = WindowsApi();
    native = HangingNative()..age = 0;
  });

  // The fake start answers after a short real delay; let it and the follow-up stop finish.
  Future<void> lateAnswers() =>
      Future<void>.delayed(const Duration(milliseconds: 200));

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

  group('ADR-185: native channel calls are bounded', () {
    test(
      'a poll whose snapshot never answers ends; nothing stopped or revoked',
      () async {
        final c = controller();
        await c.connect();
        expect(c.hasLease, true);
        final stops = native.stops;
        native.holdSnapshot = Completer<void>();
        await expectLater(c.refresh(), completes);
        expect(c.error, 'native_timeout');
        expect(c.nativeStuck, true);
        expect(native.stops, stops, reason: 'the tunnel may still run');
        expect(api.deletes, 0);
        expect(c.hasLease, true);
        // A connect tap meanwhile starts nothing.
        final creates = api.creates;
        await c.connect();
        expect(api.creates, creates);
        expect(c.error, 'native_timeout');
        // Native answers late; the late value is dropped and the next poll runs normally.
        native.holdSnapshot!.complete();
        native.holdSnapshot = null;
        await pumpEventQueue();
        expect(c.nativeStuck, false);
        await c.refresh();
        expect(c.error, isNull);
        expect(c.hasLease, true);
        expect(api.deletes, 0);
      },
    );

    test('a connect tap that waits for a hung poll (ADR-154) ends', () async {
      final c = controller();
      native.holdSnapshot = Completer<void>();
      final poll = c.refresh();
      await pumpEventQueue();
      await expectLater(
        c.connect().timeout(const Duration(seconds: 5)),
        completes,
      );
      await poll;
      expect(c.busy, false);
      expect(c.error, 'native_timeout');
      expect(api.creates, 0, reason: 'no lease while native is unknown');
      expect(native.starts, 0);
      native.holdSnapshot!.complete();
      native.holdSnapshot = null;
    });

    test('a start that answers late is stopped and never tracked', () async {
      final c = controller();
      native.holdStart = Completer<void>();
      await expectLater(
        c.connect().timeout(const Duration(seconds: 5)),
        completes,
      );
      expect(c.phase, ConnectionPhase.failed);
      expect(c.error, 'native_timeout');
      expect(c.nativeStuck, true);
      // Reconcile stopped native once before the start; the cleanup stops it again at once.
      expect(native.stops, 2, reason: 'stopped at once after the timeout');
      expect(
        api.deletes,
        1,
        reason: 'the stop answered, so the lease is revoked',
      );
      expect(c.hasLease, false);
      // While the start is unanswered, a tap starts nothing.
      await c.connect();
      expect(api.creates, 1);
      // The late start brings the tunnel up; the controller stops it again.
      native.holdStart!.complete();
      native.holdStart = null;
      await lateAnswers();
      expect(native.startsAnswered, 1);
      expect(native.up, false, reason: 'a late start must not stay up');
      expect(native.stops, 3);
      expect(c.nativeStuck, false);
      expect(c.hasLease, false);
      // A new tap reconciles with a fresh snapshot, then connects normally.
      api.freshCreates = true;
      await c.connect();
      expect(c.error, isNull);
      expect(c.hasLease, true);
      expect(native.up, true);
      expect(api.creates, 2);
    });

    test(
      'a late start whose first stop also hangs is stopped once native answers',
      () async {
        final c = controller();
        native.holdStart = Completer<void>();
        final connecting = c.connect();
        // Reconcile's stop has answered and the start is held; now stops hang too.
        await Future<void>.delayed(const Duration(milliseconds: 20));
        native.holdStop = Completer<void>();
        await connecting.timeout(const Duration(seconds: 5));
        expect(c.error, 'native_disconnect_failed');
        expect(
          api.deletes,
          0,
          reason: 'no revoke while the stop is unconfirmed',
        );
        expect(c.hasLease, true);
        // A poll meanwhile sends nothing to native and revokes nothing.
        final stops = native.stops;
        await c.refresh();
        expect(api.deletes, 0);
        expect(native.stops, stops);
        native.holdStart!.complete();
        native.holdStart = null;
        native.holdStop!.complete();
        native.holdStop = null;
        await lateAnswers();
        expect(native.up, false, reason: 'a late start must not stay up');
        expect(c.nativeStuck, false);
        // The next poll reconciles (fresh snapshot) and revokes the lease of the failed start.
        await c.refresh();
        expect(c.hasLease, false);
        expect(api.deletes, greaterThanOrEqualTo(1));
        expect(native.up, false);
      },
    );

    test(
      'a disconnect tap during a hung start ends and leaves nothing running',
      () async {
        final c = controller();
        native.holdStart = Completer<void>();
        final connecting = c.connect();
        await Future<void>.delayed(const Duration(milliseconds: 20));
        await expectLater(
          c.disconnect().timeout(const Duration(seconds: 5)),
          completes,
        );
        await connecting;
        expect(c.phase, ConnectionPhase.disconnected);
        expect(c.hasLease, false);
        native.holdStart!.complete();
        native.holdStart = null;
        await lateAnswers();
        expect(native.up, false, reason: 'a late start must not stay up');
        expect(c.nativeStuck, false);
      },
    );

    test(
      'a disconnect whose native stop never answers ends with a clear error',
      () async {
        final c = controller();
        await c.connect();
        native.holdStop = Completer<void>();
        await expectLater(
          c.disconnect().timeout(const Duration(seconds: 5)),
          completes,
        );
        expect(c.error, 'native_disconnect_failed');
        expect(api.deletes, 0, reason: 'no revoke under a tunnel that may run');
        native.holdStop!.complete();
        native.holdStop = null;
      },
    );
  });
}

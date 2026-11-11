import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/primary_action.dart';

void main() {
  group('resolvePrimaryAction', () {
    test('waiting for server close disables the action', () {
      expect(
        resolvePrimaryAction(
          waitingForServerClose: true,
          busy: true,
          mustDisconnect: true,
        ),
        PrimaryAction.none,
      );
    });

    test('busy offers cancel, which is checked before disconnect', () {
      expect(
        resolvePrimaryAction(
          waitingForServerClose: false,
          busy: true,
          mustDisconnect: true,
        ),
        PrimaryAction.cancel,
      );
    });

    test('a lease or native tunnel that is up offers disconnect', () {
      expect(
        resolvePrimaryAction(
          waitingForServerClose: false,
          busy: false,
          mustDisconnect: true,
        ),
        PrimaryAction.disconnect,
      );
    });

    test('idle offers connect', () {
      expect(
        resolvePrimaryAction(
          waitingForServerClose: false,
          busy: false,
          mustDisconnect: false,
        ),
        PrimaryAction.connect,
      );
    });
  });

  group('PrimaryActionGate', () {
    late DateTime clock;
    late PrimaryActionGate gate;
    late List<String> calls;

    Future<void> record(String name) async => calls.add(name);

    bool fire(PrimaryAction action) => gate.dispatch(
      action,
      connect: () => record('connect'),
      disconnect: () => record('disconnect'),
      cancel: () => record('cancel'),
    );

    setUp(() {
      clock = DateTime(2026, 11, 11, 12);
      gate = PrimaryActionGate(
        debounce: const Duration(milliseconds: 600),
        now: () => clock,
      );
      calls = <String>[];
    });

    test('each action maps to its own callback', () async {
      expect(fire(PrimaryAction.connect), isTrue);
      clock = clock.add(const Duration(seconds: 2));
      expect(fire(PrimaryAction.disconnect), isTrue);
      clock = clock.add(const Duration(seconds: 2));
      expect(fire(PrimaryAction.cancel), isTrue);
      await Future<void>.delayed(Duration.zero);
      expect(calls, ['connect', 'disconnect', 'cancel']);
    });

    test('none is never dispatched and does not start the window', () async {
      expect(fire(PrimaryAction.none), isFalse);
      expect(fire(PrimaryAction.connect), isTrue);
      await Future<void>.delayed(Duration.zero);
      expect(calls, ['connect']);
    });

    test('quick succession from orbit and button fires once', () async {
      // Orbit tap, then bottom-button tap 100 ms later, before any rebuild.
      expect(fire(PrimaryAction.connect), isTrue);
      clock = clock.add(const Duration(milliseconds: 100));
      expect(fire(PrimaryAction.connect), isFalse);
      clock = clock.add(const Duration(milliseconds: 100));
      expect(fire(PrimaryAction.connect), isFalse);
      await Future<void>.delayed(Duration.zero);
      expect(calls, ['connect']);
    });

    test('a tap after the window is accepted again', () async {
      expect(fire(PrimaryAction.connect), isTrue);
      clock = clock.add(const Duration(milliseconds: 600));
      expect(fire(PrimaryAction.cancel), isTrue);
      await Future<void>.delayed(Duration.zero);
      expect(calls, ['connect', 'cancel']);
    });

    test('reset clears the window', () async {
      expect(fire(PrimaryAction.connect), isTrue);
      gate.reset();
      expect(fire(PrimaryAction.disconnect), isTrue);
      await Future<void>.delayed(Duration.zero);
      expect(calls, ['connect', 'disconnect']);
    });
  });
}

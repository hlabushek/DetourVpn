import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/orbit_widgets.dart';
import 'package:vpn_client/primary_action.dart';

void main() {
  group('resolveHomeView (ADR-192)', () {
    HomeView view(
      ConnectionPhase phase, {
      bool busy = false,
      bool disconnecting = false,
      bool hasLease = false,
      bool remotePending = false,
      bool canRetryClose = true,
      String? error,
    }) => resolveHomeView(
      phase: phase,
      busy: busy || disconnecting,
      disconnecting: disconnecting,
      hasLease: hasLease,
      remotePending: remotePending,
      canRetryClose: canRetryClose,
      error: error,
    );

    void expectView(
      HomeView v,
      HomeState state,
      String title,
      PrimaryAction action,
      String label,
    ) {
      expect(v.state, state);
      expect(v.title, title);
      expect(v.action, action);
      expect(v.label, label);
    }

    test('VPN off and nothing held offers «Подключить»', () {
      expectView(
        view(ConnectionPhase.disconnected),
        HomeState.disconnected,
        'Отключено',
        PrimaryAction.connect,
        'Подключить',
      );
    });

    test('a failed attempt offers the same «Подключить», not «Повторить»', () {
      // The error texts tell the user to press «Подключить»; the tap is a plain connect.
      expectView(
        view(ConnectionPhase.failed, error: 'handshake_not_established'),
        HomeState.failed,
        'Ошибка подключения',
        PrimaryAction.connect,
        'Подключить',
      );
    });

    test('a running connect offers cancel, whatever phase it left', () {
      for (final phase in [
        ConnectionPhase.connecting,
        ConnectionPhase.disconnected, // ADR-154: a tap waiting for a poll.
        ConnectionPhase.failed,
      ]) {
        expectView(
          view(phase, busy: true),
          HomeState.connecting,
          'Подключение',
          PrimaryAction.cancel,
          'Отменить подключение',
        );
      }
    });

    test(
      'waiting for the first handshake (lease held) still offers cancel',
      () {
        expectView(
          view(ConnectionPhase.connecting, hasLease: true),
          HomeState.connecting,
          'Подключение',
          PrimaryAction.cancel,
          'Отменить подключение',
        );
      },
    );

    test('a running disconnect is not offered as a cancel', () {
      for (final phase in ConnectionPhase.values) {
        expectView(
          view(phase, disconnecting: true, hasLease: true),
          HomeState.disconnecting,
          'Отключение',
          PrimaryAction.none,
          'Отключение…',
        );
      }
    });

    test('connected offers «Отключить VPN», also without a lease (panel)', () {
      for (final lease in [true, false]) {
        expectView(
          view(ConnectionPhase.connected, hasLease: lease),
          HomeState.connected,
          'Подключено',
          PrimaryAction.disconnect,
          'Отключить VPN',
        );
      }
    });

    test(
      'recovering with nothing held offers «Подключить сейчас» (connect)',
      () {
        // Decision A, ADR-124/142: resume or native restore pending, no lease.
        expectView(
          view(ConnectionPhase.reconnecting),
          HomeState.recovering,
          'Восстановление связи',
          PrimaryAction.connect,
          'Подключить сейчас',
        );
      },
    );

    test('recovering with a tunnel or a close held offers «Отключить VPN»', () {
      for (final (lease, pending, busy) in [
        (true, false, false),
        (
          true,
          true,
          false,
        ), // ADR-191: an old row still closing, resume wanted.
        (
          false,
          true,
          false,
        ), // A close is unconfirmed: connect is never offered.
        (
          false,
          false,
          true,
        ), // A connect already runs; a tap on connect would be dropped.
      ]) {
        expectView(
          view(
            ConnectionPhase.reconnecting,
            hasLease: lease,
            remotePending: pending,
            busy: busy,
          ),
          HomeState.recovering,
          'Восстановление связи',
          PrimaryAction.disconnect,
          'Отключить VPN',
        );
      }
    });

    test(
      'VPN off while the server still closes offers a retry of that close',
      () {
        for (final phase in [
          ConnectionPhase.disconnected,
          ConnectionPhase.failed,
        ]) {
          expectView(
            view(phase, hasLease: true, remotePending: true),
            HomeState.closing,
            'Закрытие подключения',
            PrimaryAction.retryRevoke,
            'Повторить закрытие',
          );
        }
        // A controller without that retry waits instead of showing a dead button.
        expectView(
          view(
            ConnectionPhase.failed,
            hasLease: true,
            remotePending: true,
            canRetryClose: false,
          ),
          HomeState.closing,
          'Закрытие подключения',
          PrimaryAction.none,
          'Ожидание подтверждения сервера',
        );
      },
    );

    test('a failed stop or a held lease asks to disconnect first', () {
      expectView(
        view(ConnectionPhase.failed, error: 'native_disconnect_failed'),
        HomeState.stopNeeded,
        'Требуется действие',
        PrimaryAction.disconnect,
        'Отключить VPN',
      );
      expectView(
        view(ConnectionPhase.failed, hasLease: true, error: 'native_timeout'),
        HomeState.stopNeeded,
        'Требуется действие',
        PrimaryAction.disconnect,
        'Отключить VPN',
      );
    });

    test('a sign-in error offers «Войти снова» instead of connect', () {
      for (final error in ['unauthorized', 'session_device_mismatch']) {
        expectView(
          view(ConnectionPhase.failed, error: error),
          HomeState.failed,
          'Ошибка подключения',
          PrimaryAction.signIn,
          'Войти снова',
        );
        expect(
          view(ConnectionPhase.disconnected, error: error).action,
          PrimaryAction.signIn,
        );
      }
    });

    test('a quota error offers «Открыть трафик» and no connect', () {
      for (final phase in [
        ConnectionPhase.failed,
        ConnectionPhase.disconnected,
      ]) {
        final v = view(phase, error: 'insufficient_quota');
        expect(v.action, PrimaryAction.openTraffic);
        expect(v.label, 'Открыть трафик');
      }
      expect(
        view(ConnectionPhase.failed, error: 'insufficient_quota').title,
        'Ошибка подключения',
      );
    });

    test('a held lease or close still wins over sign-in and quota errors', () {
      for (final error in ['unauthorized', 'insufficient_quota']) {
        expect(
          view(ConnectionPhase.failed, hasLease: true, error: error).action,
          PrimaryAction.disconnect,
        );
        expect(
          view(
            ConnectionPhase.failed,
            hasLease: true,
            remotePending: true,
            error: error,
          ).action,
          PrimaryAction.retryRevoke,
        );
      }
    });

    test('connect is never offered while a lease or a close is held', () {
      for (final phase in ConnectionPhase.values) {
        for (final pending in [true, false]) {
          for (final busy in [true, false]) {
            final v = view(
              phase,
              hasLease: true,
              remotePending: pending,
              busy: busy,
            );
            expect(v.action, isNot(PrimaryAction.connect));
          }
        }
      }
    });

    test('the orbit follows the state', () {
      expect(
        view(ConnectionPhase.connected).orbit(hasError: true),
        OrbitState.connected,
      );
      expect(
        view(ConnectionPhase.reconnecting).orbit(hasError: true),
        OrbitState.connecting,
      );
      expect(
        view(
          ConnectionPhase.disconnected,
          disconnecting: true,
        ).orbit(hasError: false),
        OrbitState.connecting,
      );
      expect(
        view(ConnectionPhase.failed).orbit(hasError: true),
        OrbitState.error,
      );
      expect(
        view(ConnectionPhase.disconnected).orbit(hasError: false),
        OrbitState.idle,
      );
      expect(
        view(ConnectionPhase.disconnected).orbit(hasError: true),
        OrbitState.error,
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

    test(
      'retryRevoke runs the retry callback, and nothing without one',
      () async {
        expect(fire(PrimaryAction.retryRevoke), isFalse);
        expect(
          gate.dispatch(
            PrimaryAction.retryRevoke,
            connect: () => record('connect'),
            disconnect: () => record('disconnect'),
            cancel: () => record('cancel'),
            retry: () => record('retry'),
          ),
          isTrue,
        );
        await Future<void>.delayed(Duration.zero);
        expect(calls, ['retry']);
      },
    );

    test(
      'signIn and openTraffic run their callbacks, and nothing without one',
      () async {
        expect(fire(PrimaryAction.signIn), isFalse);
        expect(fire(PrimaryAction.openTraffic), isFalse);
        bool fireWith(PrimaryAction action) => gate.dispatch(
          action,
          connect: () => record('connect'),
          disconnect: () => record('disconnect'),
          cancel: () => record('cancel'),
          signIn: () => record('signIn'),
          openTraffic: () => record('openTraffic'),
        );
        expect(fireWith(PrimaryAction.signIn), isTrue);
        clock = clock.add(const Duration(seconds: 2));
        expect(fireWith(PrimaryAction.openTraffic), isTrue);
        await Future<void>.delayed(Duration.zero);
        expect(calls, ['signIn', 'openTraffic']);
      },
    );

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

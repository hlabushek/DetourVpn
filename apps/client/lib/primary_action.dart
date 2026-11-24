import 'dart:async';

import 'connection.dart';
import 'orbit_widgets.dart';

/// The single action the connection screen offers. The bottom button and the
/// orbit both resolve to one of these and both dispatch through [PrimaryActionGate].
///
/// [signIn] and [openTraffic] are the next step after an error a connect cannot fix
/// (ADR-192, owner decision B): the existing sign-out path and the Traffic tab.
enum PrimaryAction {
  none,
  cancel,
  disconnect,
  connect,
  retryRevoke,
  signIn,
  openTraffic,
}

/// Errors after which only signing in again helps (ADR-192 decision B).
const signInErrors = {'unauthorized', 'session_device_mismatch'};

/// Errors after which a connect cannot succeed until the traffic balance grows.
const quotaErrors = {'insufficient_quota'};

/// ADR-192: what the home screen shows. Each state has exactly one title, one orbit and one
/// primary action, so the picture, the status and the button never disagree.
enum HomeState {
  /// VPN off, nothing held. «Подключить».
  disconnected,

  /// A connect is running or the tunnel waits for its first handshake. «Отменить подключение».
  connecting,

  /// Tunnel up and confirmed. «Отключить VPN».
  connected,

  /// The user wants the VPN and the app (or native) is restoring it. With a tunnel still
  /// held «Отключить VPN» stops it; with nothing held «Подключить сейчас» connects at once.
  recovering,

  /// A disconnect is running. No action: a second tap would be ignored by the controller.
  disconnecting,

  /// VPN off on the phone; the server has not yet confirmed closing the old connection.
  /// «Повторить закрытие» asks the server once more (ADR-145 2.5).
  closing,

  /// Something may still run or be held and must be stopped first. «Отключить VPN».
  stopNeeded,

  /// The last attempt failed and nothing is held. «Подключить» (a plain new connect), or
  /// «Войти снова» / «Открыть трафик» when a connect cannot succeed.
  failed,
}

/// One resolved home-screen view: status title, orbit picture, primary action and its label.
class HomeView {
  const HomeView(this.state, this.action);
  final HomeState state;
  final PrimaryAction action;

  String get title => switch (state) {
    HomeState.disconnected => 'Отключено',
    HomeState.connecting => 'Подключение',
    HomeState.connected => 'Подключено',
    HomeState.recovering => 'Восстановление связи',
    HomeState.disconnecting => 'Отключение',
    HomeState.closing => 'Закрытие подключения',
    HomeState.stopNeeded => 'Требуется действие',
    HomeState.failed => 'Ошибка подключения',
  };

  /// The button text. Every action has one label wherever it is offered (connect during a
  /// restore says «сейчас»: it skips the wait); a disabled button ([PrimaryAction.none])
  /// names what the app is waiting for.
  String get label => switch (action) {
    PrimaryAction.connect =>
      state == HomeState.recovering ? 'Подключить сейчас' : 'Подключить',
    PrimaryAction.signIn => 'Войти снова',
    PrimaryAction.openTraffic => 'Открыть трафик',
    PrimaryAction.cancel => 'Отменить подключение',
    PrimaryAction.disconnect => 'Отключить VPN',
    PrimaryAction.retryRevoke => 'Повторить закрытие',
    PrimaryAction.none =>
      state == HomeState.disconnecting
          ? 'Отключение…'
          : 'Ожидание подтверждения сервера',
  };

  /// [hasError]: an error notice is shown under the status.
  OrbitState orbit({required bool hasError}) => switch (state) {
    HomeState.connecting ||
    HomeState.recovering ||
    HomeState.disconnecting => OrbitState.connecting,
    HomeState.connected => OrbitState.connected,
    HomeState.failed || HomeState.stopNeeded => OrbitState.error,
    HomeState.disconnected ||
    HomeState.closing => hasError ? OrbitState.error : OrbitState.idle,
  };
}

/// Pure decision, shared by the status, the orbit and the bottom button (ADR-192).
///
/// Order matters:
/// 1. a disconnect is running → «Отключение», no action;
/// 2. any other work is running → it is a connect (also a tap waiting for a poll, ADR-154,
///    or an automatic one): cancel it; while restoring, «Отключить VPN» stops it;
/// 3. by phase: connected → disconnect; reconnecting → disconnect while a lease or a close
///    is held, otherwise «Подключить сейчас» (connect); connecting → cancel;
/// 4. VPN off or failed: an old connection the server still closes → retry that close
///    ([canRetryClose]; otherwise wait); something still held or a failed native stop →
///    disconnect; a sign-in error → sign in again; a quota error → open Traffic;
///    otherwise → connect.
///
/// Connect is never offered while a lease is held or a close is unconfirmed, as before.
HomeView resolveHomeView({
  required ConnectionPhase phase,
  required bool busy,
  required bool disconnecting,
  required bool hasLease,
  required bool remotePending,
  required bool canRetryClose,
  String? error,
}) {
  if (disconnecting) {
    return const HomeView(HomeState.disconnecting, PrimaryAction.none);
  }
  if (busy) {
    return phase == ConnectionPhase.reconnecting
        ? const HomeView(HomeState.recovering, PrimaryAction.disconnect)
        : const HomeView(HomeState.connecting, PrimaryAction.cancel);
  }
  switch (phase) {
    case ConnectionPhase.connected:
      return const HomeView(HomeState.connected, PrimaryAction.disconnect);
    case ConnectionPhase.reconnecting:
      // Decision A: a restore with nothing held (resume or native restore pending) offers
      // an immediate connect; a held tunnel or an unconfirmed close is stopped instead.
      return HomeView(
        HomeState.recovering,
        hasLease || remotePending
            ? PrimaryAction.disconnect
            : PrimaryAction.connect,
      );
    case ConnectionPhase.connecting:
      return const HomeView(HomeState.connecting, PrimaryAction.cancel);
    case ConnectionPhase.disconnected:
    case ConnectionPhase.failed:
      if (remotePending) {
        return HomeView(
          HomeState.closing,
          canRetryClose ? PrimaryAction.retryRevoke : PrimaryAction.none,
        );
      }
      if (hasLease || error == 'native_disconnect_failed') {
        return const HomeView(HomeState.stopNeeded, PrimaryAction.disconnect);
      }
      final state = phase == ConnectionPhase.failed
          ? HomeState.failed
          : HomeState.disconnected;
      // Decision B: a connect cannot fix these, so the button offers the next step.
      if (signInErrors.contains(error)) {
        return HomeView(state, PrimaryAction.signIn);
      }
      if (quotaErrors.contains(error)) {
        return HomeView(state, PrimaryAction.openTraffic);
      }
      return HomeView(state, PrimaryAction.connect);
  }
}

/// Runs a [PrimaryAction] at most once per [debounce] window, across both controls.
///
/// A second tap on either control inside the window (for example the orbit and the
/// bottom button hit in quick succession, before the screen rebuilds) is ignored, so
/// connect/disconnect/cancel can never be requested twice by one gesture burst.
/// [PrimaryAction.none] is never dispatched.
class PrimaryActionGate {
  PrimaryActionGate({
    this.debounce = const Duration(milliseconds: 600),
    DateTime Function()? now,
  }) : _now = now ?? DateTime.now;

  /// The one gate used by the connection screen. Process-wide on purpose: the screen
  /// is stateless and there is exactly one primary action on it.
  static final PrimaryActionGate shared = PrimaryActionGate();

  final Duration debounce;
  final DateTime Function() _now;
  DateTime? _lastAccepted;

  /// Returns true when the action was dispatched. Errors from the callbacks are the
  /// controller's business and are not swallowed here.
  bool dispatch(
    PrimaryAction action, {
    required Future<void> Function() connect,
    required Future<void> Function() disconnect,
    required Future<void> Function() cancel,
    Future<void> Function()? retry,
    Future<void> Function()? signIn,
    Future<void> Function()? openTraffic,
  }) {
    if (action == PrimaryAction.none) return false;
    if (action == PrimaryAction.retryRevoke && retry == null) return false;
    if (action == PrimaryAction.signIn && signIn == null) return false;
    if (action == PrimaryAction.openTraffic && openTraffic == null)
      return false;
    final now = _now();
    final last = _lastAccepted;
    if (last != null && now.difference(last) < debounce) return false;
    _lastAccepted = now;
    final Future<void> Function() run = switch (action) {
      PrimaryAction.connect => connect,
      PrimaryAction.disconnect => disconnect,
      PrimaryAction.cancel => cancel,
      PrimaryAction.retryRevoke => retry!,
      PrimaryAction.signIn => signIn!,
      PrimaryAction.openTraffic => openTraffic!,
      PrimaryAction.none => () async {},
    };
    unawaited(run());
    return true;
  }

  /// Forgets the last accepted tap. Tests only.
  void reset() => _lastAccepted = null;
}

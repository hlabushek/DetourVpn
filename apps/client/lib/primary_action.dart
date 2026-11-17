import 'dart:async';

/// The single action the connection screen offers. The bottom button and the
/// orbit both resolve to one of these and both dispatch through [PrimaryActionGate].
enum PrimaryAction { none, cancel, disconnect, connect, retryRevoke }

/// Pure decision, shared by the bottom button and the orbit.
///
/// Order matters and mirrors the bottom button's previous rules:
/// 1. waiting for the server to confirm a remote close → retry that close now (ADR-145; it
///    was a disabled button before), or cancel while a connect is still running;
/// 2. busy (connecting / closing) → cancel;
/// 3. a lease or native tunnel must be torn down → disconnect;
/// 4. otherwise → connect.
PrimaryAction resolvePrimaryAction({
  required bool waitingForServerClose,
  required bool busy,
  required bool mustDisconnect,
}) {
  if (waitingForServerClose) {
    return busy ? PrimaryAction.cancel : PrimaryAction.retryRevoke;
  }
  if (busy) return PrimaryAction.cancel;
  if (mustDisconnect) return PrimaryAction.disconnect;
  return PrimaryAction.connect;
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
  }) {
    if (action == PrimaryAction.none) return false;
    if (action == PrimaryAction.retryRevoke && retry == null) return false;
    final now = _now();
    final last = _lastAccepted;
    if (last != null && now.difference(last) < debounce) return false;
    _lastAccepted = now;
    final Future<void> Function() run = switch (action) {
      PrimaryAction.connect => connect,
      PrimaryAction.disconnect => disconnect,
      PrimaryAction.cancel => cancel,
      PrimaryAction.retryRevoke => retry!,
      PrimaryAction.none => () async {},
    };
    unawaited(run());
    return true;
  }

  /// Forgets the last accepted tap. Tests only.
  void reset() => _lastAccepted = null;
}

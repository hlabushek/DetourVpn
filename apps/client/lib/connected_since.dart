import 'dart:async';

/// When the UI first saw a connection up, kept across app restarts so the home-screen timer
/// continues after the app is killed or reopened while the VPN stays up. Display only.
///
/// Native reports no tunnel start time and the server's connection record has no timestamp, so
/// this is the first moment the UI observed the connection connected, not the native start.
class ConnectedSince {
  const ConnectedSince(this.id, this.sinceMs);
  final String id;
  final int sinceMs;
}

abstract interface class ConnectedSinceStore {
  Future<ConnectedSince?> readConnectedSince();

  /// Null [value] clears the record.
  Future<void> writeConnectedSince(ConnectedSince? value);
}

final _uuid = RegExp(
  r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
);

/// Follows the live connection and keeps its start time.
///
/// [observe] gets the current connection's id while it is up (connected or reconnecting) and
/// null otherwise. A new id starts the timer (or resumes the stored start of that same id after
/// a restart); a null after a connection this process saw clears it. A null before any
/// connection (app start, before a running tunnel is adopted) keeps the stored record.
class ConnectedSinceTracker {
  ConnectedSinceTracker(this.store, {int Function()? nowMs})
    : _nowMs = nowMs ?? (() => DateTime.now().millisecondsSinceEpoch);
  final ConnectedSinceStore? store;
  final int Function() _nowMs;

  String? _id;
  int? _sinceMs;
  bool _loaded = false;
  ConnectedSince? _stored;
  Future<void> _queue = Future.value();

  /// Epoch milliseconds when the current connection was first seen up; null when none.
  int? get sinceMs => _sinceMs;

  /// Returns true (asynchronously) when [sinceMs] changed.
  Future<bool> observe(String? id) {
    final result = _queue.then((_) => _observe(id));
    _queue = result.then((_) {}, onError: (_) {});
    return result;
  }

  Future<bool> _observe(String? id) async {
    if (id == _id) return false;
    if (id == null) {
      _id = null;
      _sinceMs = null;
      _stored = null;
      await _write(null);
      return true;
    }
    if (!_loaded) {
      _loaded = true;
      try {
        _stored = await store?.readConnectedSince();
      } catch (_) {
        _stored = null; // No stored record: the timer starts now.
      }
    }
    final stored = _stored;
    _id = id;
    if (stored != null && stored.id == id && stored.sinceMs > 0) {
      _sinceMs = stored.sinceMs;
      return true;
    }
    _sinceMs = _nowMs();
    _stored = ConnectedSince(id, _sinceMs!);
    if (_uuid.hasMatch(id)) await _write(_stored);
    return true;
  }

  Future<void> _write(ConnectedSince? value) async {
    try {
      await store?.writeConnectedSince(value);
    } catch (_) {
      // Display only: without storage the timer restarts after an app restart.
    }
  }
}

/// «02:14:35»: hours (two digits or more), minutes and seconds. Negative values count as zero.
String connectedDurationText(int elapsedMs) {
  final total = elapsedMs < 0 ? 0 : elapsedMs ~/ 1000;
  String two(int v) => v.toString().padLeft(2, '0');
  return '${two(total ~/ 3600)}:${two(total % 3600 ~/ 60)}:${two(total % 60)}';
}

/// «Подключено 02:14:35»; null without a start time.
String? connectedTimerText(int? sinceMs, int nowMs) => sinceMs == null
    ? null
    : 'Подключено ${connectedDurationText(nowMs - sinceMs)}';

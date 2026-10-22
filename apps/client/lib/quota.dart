import 'package:flutter/foundation.dart';

import 'api.dart';

const maxBytes = 9223372036854775807;

int _integer(Object? value, {int minimum = 0}) {
  if (value is! int || value < minimum || value > maxBytes) {
    throw const ClientError('invalid_response');
  }
  return value;
}

/// Exact decimal text: no floating point conversion, including int64 limits.
String byteText(int value) {
  final digits = value.abs().toString();
  final grouped = digits.replaceAllMapped(
    RegExp(r'(\d)(?=(\d{3})+$)'),
    (match) => '${match[1]} ',
  );
  return '${value < 0 ? '−' : ''}$grouped байт';
}

class QuotaSnapshot {
  QuotaSnapshot.fromJson(Map<String, dynamic> json)
    : granted = _integer(json['granted_bytes']),
      used = _integer(json['used_bytes']),
      available = _integer(json['available_bytes']),
      reserved = _integer(json['reserved_bytes']),
      revision = _integer(json['revision']) {
    // Subtractions avoid overflowing signed int64 when checking remote values.
    if (used > granted ||
        reserved > granted - used ||
        available != granted - used - reserved) {
      throw const ClientError('invalid_response');
    }
  }
  final int granted, used, available, reserved, revision;
}

class QuotaEntry {
  QuotaEntry.fromJson(Map<String, dynamic> json)
    : sequence = _integer(json['sequence'], minimum: 1),
      delta = _integer(json['delta_bytes'], minimum: -maxBytes),
      balance = _integer(json['balance_after_bytes']),
      kind = json['kind'] as String,
      source = json['source_type'] as String,
      createdAt = DateTime.parse(json['created_at'] as String).toUtc() {
    if (!{
          'registration',
          'internal',
          'usage',
          'tunnel_lease',
          'reward',
        }.contains(source) ||
        (kind != 'credit' && kind != 'debit') ||
        (kind == 'credit' ? delta < 0 : delta >= 0) ||
        (delta == 0 && source != 'registration')) {
      throw const ClientError('invalid_response');
    }
  }
  final int sequence, delta, balance;
  final String kind, source;
  final DateTime createdAt;
  String get label => switch (source) {
    'registration' => 'Начальный трафик',
    'internal' => 'Начисление или корректировка',
    'usage' => 'Расход трафика',
    'tunnel_lease' => 'Расчёт подключения',
    'reward' => 'Бонусный трафик',
    _ => 'Операция',
  };
}

/// Account-scoped RAM only. Refresh publishes summary + history atomically.
class QuotaController extends ChangeNotifier {
  QuotaController(this.api);
  final TunnelApi api;
  QuotaSnapshot? snapshot;
  List<QuotaEntry> entries = const [];
  int? nextBefore;
  DateTime? updatedAt;
  bool stale = true;
  bool loading = false;
  String? error;
  int _epoch = 0;
  bool _disposed = false;
  static const pageSize = 20;
  static const maxEntries = 500;
  bool get capped => entries.length >= maxEntries && nextBefore != null;
  void _emit() {
    if (!_disposed) notifyListeners();
  }

  void clear() {
    ++_epoch;
    snapshot = null;
    entries = const [];
    nextBefore = null;
    updatedAt = null;
    stale = true;
    loading = false;
    error = null;
    _emit();
  }

  Future<void> refresh() => _load(false);
  Future<void> more() => _load(true);

  Future<void> _load(bool append) async {
    if (_disposed ||
        loading ||
        (append && (snapshot == null || nextBefore == null || capped || stale)))
      return;
    final epoch = _epoch;
    loading = true;
    error = null;
    _emit();
    try {
      final summary = append
          ? snapshot!
          : QuotaSnapshot.fromJson(await api.call('GET', '/api/v1/quota'));
      if (_disposed || epoch != _epoch) return;
      final before = append ? nextBefore : null;
      final path =
          '/api/v1/quota/history?limit=$pageSize&snapshot_revision=${summary.revision}'
          '${before == null ? '' : '&before_sequence=$before'}';
      final page = await api.call('GET', path);
      if (_disposed || epoch != _epoch) return;
      if (_integer(page['snapshot_revision']) != summary.revision ||
          page['items'] is! List) {
        throw const ClientError('invalid_response');
      }
      final rows = (page['items'] as List)
          .map((row) => QuotaEntry.fromJson(row as Map<String, dynamic>))
          .toList();
      var expected = before == null ? summary.revision : before - 1;
      for (final row in rows) {
        if (row.sequence != expected)
          throw const ClientError('invalid_response');
        expected--;
      }
      final cursor = page['next_before_sequence'];
      if (rows.length > pageSize ||
          (cursor != null &&
              (rows.length != pageSize ||
                  _integer(cursor, minimum: 1) != rows.last.sequence ||
                  expected == 0)) ||
          (cursor == null && expected != 0)) {
        throw const ClientError('invalid_response');
      }
      snapshot = summary;
      entries = List.unmodifiable([if (append) ...entries, ...rows]);
      nextBefore = cursor as int?;
      if (!append) updatedAt = DateTime.now();
      stale = false;
    } catch (failure) {
      if (_disposed || epoch != _epoch) return;
      error = failure is ClientError ? failure.code : 'invalid_response';
      // No stale account data after an explicit auth rejection.
      if ({
        'unauthorized',
        'session_expired',
        'device_revoked',
      }.contains(error)) {
        snapshot = null;
        entries = const [];
        nextBefore = null;
        updatedAt = null;
      }
      // A failed next page can be retried with the unchanged cursor.
      if (!append) stale = true;
    } finally {
      if (!_disposed && epoch == _epoch) {
        loading = false;
        _emit();
      }
    }
  }

  @override
  void dispose() {
    _disposed = true;
    ++_epoch;
    super.dispose();
  }
}

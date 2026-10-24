import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/quota.dart';
import 'package:vpn_client/quota_screen.dart';

// TEST-ONLY responses. Do not prove API/database or VPN traffic accounting.
Map<String, dynamic> summary({int revision = 0, int reserved = 100}) => {
  'granted_bytes': 9007199254740993,
  'used_bytes': 3,
  'reserved_bytes': reserved,
  'available_bytes': 9007199254740990 - reserved,
  'revision': revision,
};

Map<String, dynamic> entry(int sequence) => {
  'sequence': sequence,
  'kind': 'credit',
  'delta_bytes': 1,
  'balance_after_bytes': sequence,
  'source_type': 'internal',
  'created_at': '2026-10-21T12:00:00Z',
};

class QuotaApi implements TunnelApi {
  Map<String, dynamic> totals = summary();
  Map<String, dynamic> page = {
    'items': <Map<String, dynamic>>[],
    'snapshot_revision': 0,
    'next_before_sequence': null,
  };
  String? failure;
  Completer<void>? gate;
  final paths = <String>[];
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    paths.add(path);
    if (gate != null) await gate!.future;
    if (failure != null) throw ClientError(failure!);
    return path == '/api/v1/quota' ? totals : page;
  }
}

void main() {
  test('peer window history preserves exact integer byte debit', () {
    final json = entry(2);
    json['kind'] = 'debit';
    json['source_type'] = 'peer_window';
    json['delta_bytes'] = -65536;
    final value = QuotaEntry.fromJson(json);
    expect(value.delta, -65536);
    expect(value.label, 'Расход VPN-трафика');
  });

  test('unavailable funding is exact and separate from measured usage', () {
    final json = summary();
    json['unavailable_bytes'] = 9007199254740000;
    json['available_bytes'] = 890;
    final snapshot = QuotaSnapshot.fromJson(json);
    expect(snapshot.unavailable, 9007199254740000);
    expect(snapshot.used, 3);
    expect(snapshot.available, 890);
    json['unavailable_bytes'] = 9007199254740991;
    expect(() => QuotaSnapshot.fromJson(json), throwsA(isA<ClientError>()));
  });

  test(
    'bounds loaded history to 500 rows and resets cursor on refresh',
    () async {
      final api = QuotaApi()..totals = summary(revision: 501);
      final c = QuotaController(api);
      addTearDown(c.dispose);
      for (var top = 501; top > 1; top -= 20) {
        api.page = {
          'items': List.generate(20, (i) => entry(top - i)),
          'snapshot_revision': 501,
          'next_before_sequence': top - 19,
        };
        if (top == 501) {
          await c.refresh();
        } else {
          await c.more();
        }
      }
      expect(c.entries.length, 500);
      expect(c.capped, true);
      final requests = api.paths.length;
      await c.more();
      expect(api.paths.length, requests);
      api.page = {
        'items': List.generate(20, (i) => entry(501 - i)),
        'snapshot_revision': 501,
        'next_before_sequence': 482,
      };
      await c.refresh();
      expect(c.entries.length, 20);
      expect(c.capped, false);
    },
  );
  test(
    'exact integer byte formatting beyond JS safe integer and at int64 max',
    () {
      expect(byteText(9007199254740993), '9 007 199 254 740 993 байт');
      expect(byteText(maxBytes), '9 223 372 036 854 775 807 байт');
      expect(byteText(-1048576), '−1 048 576 байт');
      expect(byteText(0), '0 байт');
    },
  );

  test('available excludes both charged usage and outstanding reservation', () {
    final value = QuotaSnapshot.fromJson(summary());
    expect(value.available, 9007199254740890);
    expect(value.reserved, 100);
    expect(
      QuotaSnapshot.fromJson(summary(reserved: 0)).available,
      value.available + 100,
    );
  });

  test('reject malformed, floating, overflowing and inconsistent totals', () {
    for (final bad in [
      {...summary(), 'available_bytes': -1},
      {...summary(), 'used_bytes': 3.0},
      {...summary(), 'granted_bytes': '9007199254740993'},
      {...summary(), 'reserved_bytes': maxBytes},
      {...summary(), 'available_bytes': maxBytes},
    ]) {
      expect(() => QuotaSnapshot.fromJson(bad), throwsA(isA<ClientError>()));
    }
  });

  test('loads a consistent empty history and exact summary', () async {
    final api = QuotaApi();
    final c = QuotaController(api);
    addTearDown(c.dispose);
    await c.refresh();
    expect(c.error, null);
    expect(c.snapshot!.available, 9007199254740890);
    expect(c.entries, isEmpty);
    expect(c.stale, false);
    expect(
      api.paths.last,
      '/api/v1/quota/history?limit=20&snapshot_revision=0',
    );
  });

  test(
    'pins subsequent pages to original revision despite concurrent credit',
    () async {
      final api = QuotaApi()
        ..totals = summary(revision: 21)
        ..page = {
          'items': List.generate(20, (i) => entry(21 - i)),
          'snapshot_revision': 21,
          'next_before_sequence': 2,
        };
      final c = QuotaController(api);
      addTearDown(c.dispose);
      await c.refresh();
      api.totals = summary(revision: 22);
      api.page = {
        'items': [entry(1)],
        'snapshot_revision': 21,
        'next_before_sequence': null,
      };
      await c.more();
      expect(c.entries.length, 21);
      expect(c.snapshot!.revision, 21);
      expect(c.nextBefore, null);
      expect(
        api.paths.last,
        '/api/v1/quota/history?limit=20&snapshot_revision=21&before_sequence=2',
      );
    },
  );

  test(
    'refresh failure preserves snapshot with stale marker; retry replaces it',
    () async {
      final api = QuotaApi();
      final c = QuotaController(api);
      addTearDown(c.dispose);
      await c.refresh();
      final previous = c.snapshot;
      final timestamp = c.updatedAt;
      api.failure = 'network_unavailable';
      await c.refresh();
      expect(c.snapshot, same(previous));
      expect(c.updatedAt, timestamp);
      expect(c.stale, true);
      api.failure = null;
      api.totals = summary(reserved: 0);
      await c.refresh();
      expect(c.stale, false);
      expect(c.snapshot!.reserved, 0);
    },
  );

  test(
    'failed page is retried without losing or duplicating loaded entries',
    () async {
      final api = QuotaApi()
        ..totals = summary(revision: 21)
        ..page = {
          'items': List.generate(20, (i) => entry(21 - i)),
          'snapshot_revision': 21,
          'next_before_sequence': 2,
        };
      final c = QuotaController(api);
      addTearDown(c.dispose);
      await c.refresh();
      api.failure = 'network_unavailable';
      await c.more();
      expect(c.entries.length, 20);
      expect(c.nextBefore, 2);
      api.failure = null;
      api.page = {
        'items': [entry(1)],
        'snapshot_revision': 21,
        'next_before_sequence': null,
      };
      await c.more();
      expect(c.entries.map((e) => e.sequence).toSet().length, 21);
    },
  );

  test(
    'rejects mixed revisions, missing rows, duplicates and invalid cursors',
    () async {
      for (final page in [
        {
          'items': [entry(2), entry(1)],
          'snapshot_revision': 3,
          'next_before_sequence': null,
        },
        {
          'items': [entry(2), entry(2)],
          'snapshot_revision': 2,
          'next_before_sequence': null,
        },
        {
          'items': <Map<String, dynamic>>[],
          'snapshot_revision': 2,
          'next_before_sequence': null,
        },
        {
          'items': [entry(2)],
          'snapshot_revision': 2,
          'next_before_sequence': 2,
        },
        {
          'items': [
            {...entry(2), 'delta_bytes': -1},
          ],
          'snapshot_revision': 2,
          'next_before_sequence': null,
        },
      ]) {
        final api = QuotaApi()
          ..totals = summary(revision: 2)
          ..page = page;
        final c = QuotaController(api);
        await c.refresh();
        expect(c.error, 'invalid_response');
        expect(c.snapshot, null);
        expect(c.entries, isEmpty);
        c.dispose();
      }
    },
  );

  test(
    'clear during HTTP request prevents old account data from returning',
    () async {
      final api = QuotaApi()..gate = Completer<void>();
      final c = QuotaController(api);
      addTearDown(c.dispose);
      final pending = c.refresh();
      c.clear();
      api.gate!.complete();
      await pending;
      expect(c.snapshot, null);
      expect(c.entries, isEmpty);
      expect(c.loading, false);
      expect(api.paths.length, 1);
      await c.refresh();
      expect(c.snapshot, isNotNull);
    },
  );

  test('auth rejection erases prior financial data', () async {
    final api = QuotaApi();
    final c = QuotaController(api);
    addTearDown(c.dispose);
    await c.refresh();
    api.failure = 'session_expired';
    await c.refresh();
    expect(c.snapshot, null);
    expect(c.updatedAt, null);
    expect(c.error, 'session_expired');
  });

  test(
    'serializes double refresh and safely ignores disposed responses',
    () async {
      final api = QuotaApi()..gate = Completer<void>();
      final c = QuotaController(api);
      final pending = c.refresh();
      await c.refresh();
      expect(api.paths.length, 1);
      c.dispose();
      api.gate!.complete();
      await pending;
      expect(api.paths.length, 1);
    },
  );

  testWidgets(
    'quota screen shows exact sums, empty state, and stale error at large font',
    (tester) async {
      final api = QuotaApi();
      final c = QuotaController(api);
      addTearDown(c.dispose);
      await c.refresh();
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: QuotaScreen(controller: c),
          ),
        ),
      );
      expect(find.text('Доступно: 9 007 199 254 740 890 байт'), findsOneWidget);
      await tester.scrollUntilVisible(find.text('Операций пока нет'), 300);
      expect(tester.takeException(), null);
      api.failure = 'network_unavailable';
      await c.refresh();
      await tester.pump();
      await tester.scrollUntilVisible(
        find.textContaining('Данные устарели'),
        -300,
      );
      expect(find.textContaining('Данные устарели'), findsOneWidget);
      expect(tester.takeException(), null);
    },
  );
}

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/app_update.dart';
import 'package:vpn_client/settings_screen.dart';

Map<String, dynamic> manifest({
  Object code = 30,
  Object minimum = 28,
  Object url = 'https://updates.example.test/detourvpn.apk',
  Object sha =
      '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  Object size = 1024,
}) => {
  'version_code': code,
  'version_name': '0.1.0-beta.29',
  'min_supported_version_code': minimum,
  'apk_url': url,
  'apk_sha256': sha,
  'apk_size_bytes': size,
  'release_notes_ru': 'Исправления',
};

const keyId = '0123456789abcdef';

/// MOCK envelope: the payload is real base64 of [value]; the signature is NOT a real signature,
/// because the fake native side below decides "verified" itself (Java tests cover Ed25519).
Map<String, dynamic> envelope(Map<String, dynamic> value) => {
  'payload': base64.encode(utf8.encode(jsonEncode(value))),
  'signature': '${'A' * 86}==',
  'key_id': keyId,
};

SignedManifest signedOf(Map<String, dynamic> value) =>
    SignedManifest.parse(envelope(value))!;

/// MOCK: fixed manifest response; no network.
class FakeApi implements TunnelApi {
  FakeApi(this.value, {this.error});
  final Map<String, dynamic> value;
  final String? error;
  final paths = <String>[];
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    paths.add('$method $path');
    if (error != null) throw ClientError(error!);
    return value;
  }
}

/// MOCK: installed version and install outcome; nothing is downloaded.
class FakeUpdateNative implements UpdateNative {
  FakeUpdateNative(
    this.installed, {
    this.outcome = 'installer_opened',
    this.verifies = true,
  });
  final int? installed;
  final String outcome;
  final bool verifies;
  int installs = 0;
  final verified = <SignedManifest>[];
  @override
  Future<int?> installedVersionCode() async => installed;
  @override
  Future<String?> installedVersionName() async => 'test-$installed';
  @override
  Future<String?> verifyManifest(SignedManifest signed) async {
    verified.add(signed);
    return verifies ? utf8.decode(base64.decode(signed.payload)) : null;
  }

  @override
  Future<String> install(SignedManifest signed) async {
    installs++;
    return outcome;
  }

  @override
  Future<UpdateProgress?> progress() async =>
      const UpdateProgress(UpdateNativePhase.idle, 0, 0, '');

  @override
  Future<void> cancel() async {}
}

/// MOCK (ADR-188): a download the test drives by hand. Nothing is downloaded; [snapshot] is what
/// "native" reports, and [finish] answers the pending install call.
class SteppedUpdateNative extends FakeUpdateNative {
  SteppedUpdateNative({this.answerBusy = false}) : super(27);
  final bool answerBusy;
  Completer<String>? _pending;
  UpdateProgress snapshot = const UpdateProgress(
    UpdateNativePhase.downloading,
    0,
    0,
    '',
  );
  int cancels = 0;

  void finish(String outcome) => _pending?.complete(outcome);

  @override
  Future<String> install(SignedManifest signed) {
    installs++;
    if (answerBusy) return Future.value('busy');
    _pending = Completer<String>();
    return _pending!.future;
  }

  @override
  Future<UpdateProgress?> progress() async => snapshot;

  @override
  Future<void> cancel() async {
    cancels++;
    finish('cancelled');
  }
}

void main() {
  group('UpdateManifest.parse', () {
    test('accepts a valid manifest', () {
      final value = UpdateManifest.parse(manifest())!;
      expect(value.versionCode, 30);
      expect(value.minSupportedVersionCode, 28);
    });
    test('rejects http, bad hash, bad sizes, minimum above latest', () {
      expect(
        UpdateManifest.parse(manifest(url: 'http://a.test/x.apk')),
        isNull,
      );
      expect(UpdateManifest.parse(manifest(url: 'https://u@a.test/x')), isNull);
      expect(UpdateManifest.parse(manifest(sha: 'ABC')), isNull);
      expect(UpdateManifest.parse(manifest(sha: 'f' * 63)), isNull);
      expect(UpdateManifest.parse(manifest(size: 0)), isNull);
      expect(UpdateManifest.parse(manifest(code: '30')), isNull);
      expect(UpdateManifest.parse(manifest(minimum: 31)), isNull);
      expect(UpdateManifest.parse(<String, dynamic>{}), isNull);
    });
  });

  group('decideUpdate', () {
    final value = UpdateManifest.parse(manifest())!;
    test('below minimum is blocking', () {
      expect(decideUpdate(27, value), UpdateDecision.blocking);
    });
    test('at or above minimum but older is optional', () {
      expect(decideUpdate(28, value), UpdateDecision.optional);
      expect(decideUpdate(29, value), UpdateDecision.optional);
    });
    test('same or newer installed never updates (no downgrade)', () {
      expect(decideUpdate(30, value), UpdateDecision.none);
      expect(decideUpdate(31, value), UpdateDecision.none);
    });
  });

  group('checkForUpdate', () {
    test(
      'asks the signed endpoint and uses only the verified payload',
      () async {
        final api = FakeApi(envelope(manifest()));
        final native = FakeUpdateNative(27);
        final offer = await checkForUpdate(api, native);
        expect(api.paths, ['GET /api/v1/app/signed-update-manifest']);
        expect(native.verified.single.keyId, keyId);
        expect(offer!.decision, UpdateDecision.blocking);
        expect(offer.signed.payload, envelope(manifest())['payload']);
      },
    );
    test('a signature the native side rejects gives nothing', () async {
      final native = FakeUpdateNative(27, verifies: false);
      expect(
        await checkForUpdate(FakeApi(envelope(manifest())), native),
        isNull,
      );
      expect(native.verified, hasLength(1));
    });
    test('an unsigned or malformed envelope is never even verified', () async {
      for (final bad in <Map<String, dynamic>>[
        manifest(),
        {...envelope(manifest()), 'extra': 1},
        {...envelope(manifest()), 'key_id': 'ABC'},
        {...envelope(manifest()), 'signature': 'short'},
      ]) {
        final native = FakeUpdateNative(27);
        expect(await checkForUpdate(FakeApi(bad), native), isNull);
        expect(native.verified, isEmpty);
      }
    });
    test(
      'unavailable manifest, unknown version or downgrade gives nothing',
      () async {
        expect(
          await checkForUpdate(
            FakeApi(envelope(manifest()), error: 'update_unavailable'),
            FakeUpdateNative(27),
          ),
          isNull,
        );
        expect(
          await checkForUpdate(
            FakeApi(envelope(manifest())),
            FakeUpdateNative(null),
          ),
          isNull,
        );
        expect(
          await checkForUpdate(
            FakeApi(envelope(manifest())),
            FakeUpdateNative(40),
          ),
          isNull,
        );
        expect(
          await checkForUpdate(
            FakeApi(envelope(manifest(sha: 'x'))),
            FakeUpdateNative(1),
          ),
          isNull,
        );
      },
    );
  });

  Future<void> open(
    WidgetTester tester,
    UpdateOffer offer,
    UpdateNative native,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showUpdateDialog(context, offer, native),
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  testWidgets('blocking dialog has no "later" and survives back', (
    tester,
  ) async {
    final native = FakeUpdateNative(27);
    final offer = UpdateOffer(
      UpdateManifest.parse(manifest())!,
      UpdateDecision.blocking,
      signedOf(manifest()),
    );
    await open(tester, offer, native);
    expect(find.text('Нужно обновить приложение'), findsOneWidget);
    expect(find.text('Позже'), findsNothing);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Нужно обновить приложение'), findsOneWidget);
    await tester.tap(find.text('Скачать'));
    await tester.pumpAndSettle();
    expect(native.installs, 1);
    expect(
      find.text('Подтвердите установку в системном окне Android.'),
      findsOneWidget,
    );
  });

  testWidgets('optional dialog can be dismissed', (tester) async {
    final offer = UpdateOffer(
      UpdateManifest.parse(manifest())!,
      UpdateDecision.optional,
      signedOf(manifest()),
    );
    await open(tester, offer, FakeUpdateNative(29));
    expect(find.text('Доступно обновление'), findsOneWidget);
    await tester.tap(find.text('Позже'));
    await tester.pumpAndSettle();
    expect(find.text('Доступно обновление'), findsNothing);
  });

  testWidgets('a signature refused at install is reported in Russian', (
    tester,
  ) async {
    final native = FakeUpdateNative(27, outcome: 'manifest_unverified');
    final offer = UpdateOffer(
      UpdateManifest.parse(manifest())!,
      UpdateDecision.blocking,
      signedOf(manifest()),
    );
    await open(tester, offer, native);
    await tester.tap(find.text('Скачать'));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Не удалось проверить подпись обновления. Обновление не установлено.',
      ),
      findsOneWidget,
    );
  });

  testWidgets('verification failure is reported in Russian', (tester) async {
    final native = FakeUpdateNative(27, outcome: 'hash_mismatch');
    final offer = UpdateOffer(
      UpdateManifest.parse(manifest())!,
      UpdateDecision.blocking,
      signedOf(manifest()),
    );
    await open(tester, offer, native);
    await tester.tap(find.text('Скачать'));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Загруженный файл не прошёл проверку и удалён. Попробуйте позже.',
      ),
      findsOneWidget,
    );
  });

  group('manual and repeated checks', () {
    test('check statuses tell available, up to date and failed apart', () async {
      final available = await checkForUpdateDetailed(
        FakeApi(envelope(manifest())),
        FakeUpdateNative(29),
      );
      expect(available.status, UpdateCheckStatus.available);
      expect(available.offer, isNotNull);
      expect(
        (await checkForUpdateDetailed(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(30),
        )).status,
        UpdateCheckStatus.upToDate,
      );
      expect(
        (await checkForUpdateDetailed(
          FakeApi(envelope(manifest()), error: 'update_unavailable'),
          FakeUpdateNative(29),
        )).status,
        UpdateCheckStatus.upToDate,
      );
      for (final failing in [
        checkForUpdateDetailed(
          FakeApi(envelope(manifest()), error: 'network_unavailable'),
          FakeUpdateNative(29),
        ),
        // Android below 13 or a bad signature: the manifest cannot be verified.
        checkForUpdateDetailed(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(29, verifies: false),
        ),
        checkForUpdateDetailed(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(null),
        ),
      ]) {
        final result = await failing;
        expect(result.status, UpdateCheckStatus.failed);
        expect(result.offer, isNull);
      }
      expect(
        updateCheckText(UpdateCheckStatus.upToDate),
        contains('последняя версия'),
      );
      expect(updateCheckText(UpdateCheckStatus.failed), contains('Android 13'));
    });

    test(
      'automatic checks run at most every 6 hours; manual ones always',
      () async {
        var now = DateTime.utc(2026, 11, 11, 12);
        final api = FakeApi(envelope(manifest()));
        final scheduler = UpdateScheduler(
          api,
          FakeUpdateNative(30),
          now: () => now,
        );
        Future<void> none(UpdateOffer _) async {}
        expect(await scheduler.run(manual: false, show: none), isNotNull);
        now = now.add(const Duration(hours: 5));
        expect(
          await scheduler.run(manual: false, show: none),
          isNull,
        ); // not due
        expect(await scheduler.run(manual: true, show: none), isNotNull);
        now = now.add(const Duration(hours: 6));
        expect(await scheduler.run(manual: false, show: none), isNotNull);
        expect(api.paths, hasLength(3));
      },
    );

    test('an open update dialog blocks a second one', () async {
      final scheduler = UpdateScheduler(
        FakeApi(envelope(manifest())),
        FakeUpdateNative(29),
      );
      final open = Completer<void>();
      var shown = 0;
      Future<void> show(UpdateOffer _) {
        shown++;
        return open.future;
      }

      final first = scheduler.run(manual: false, show: show);
      await Future<void>.delayed(Duration.zero);
      expect(await scheduler.run(manual: true, show: show), isNull);
      open.complete();
      expect((await first)!.status, UpdateCheckStatus.available);
      expect(shown, 1);
    });
  });

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 200));
    }
  }

  testWidgets('Settings: the manual check says when there is nothing new', (
    tester,
  ) async {
    var checks = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(
          onTheme: () {},
          controller: null,
          onCheckUpdates: () async {
            checks++;
            return const UpdateCheck(UpdateCheckStatus.upToDate);
          },
        ),
      ),
    );
    await settle(tester);
    await tester.tap(find.text('Проверить обновления'));
    await settle(tester);
    expect(checks, 1);
    expect(
      find.text('Установлена последняя версия DetourVPN.'),
      findsOneWidget,
    );
  });

  testWidgets('Settings: no updater, no row', (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: SettingsScreen(onTheme: () {}, controller: null)),
    );
    await settle(tester);
    expect(find.text('Проверить обновления'), findsNothing);
  });

  group('installed version', () {
    test('label combines name and code, drops unusable values', () {
      expect(
        installedVersionLabel('0.1.0-beta.31', 32),
        '0.1.0-beta.31 (код 32)',
      );
      expect(installedVersionLabel(' 0.1.0 ', null), '0.1.0');
      expect(installedVersionLabel(null, 32), 'код 32');
      expect(installedVersionLabel('', 0), isNull);
      expect(installedVersionLabel('bad\nname', -1), isNull);
    });

    testWidgets('Settings shows the installed version on the update row', (
      tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          home: SettingsScreen(
            onTheme: () {},
            controller: null,
            onCheckUpdates: () async =>
                const UpdateCheck(UpdateCheckStatus.upToDate),
            installedVersion: '0.1.0-beta.31 (код 32)',
          ),
        ),
      );
      await settle(tester);
      expect(
        find.text('Установлена версия 0.1.0-beta.31 (код 32)'),
        findsOneWidget,
      );
    });
  });

  testWidgets('an available version is named and offers «Скачать»', (
    tester,
  ) async {
    final native = FakeUpdateNative(27);
    final offer = UpdateOffer(
      UpdateManifest.parse(manifest())!,
      UpdateDecision.optional,
      signedOf(manifest()),
    );
    await open(tester, offer, native);
    expect(find.text('Доступна версия 0.1.0-beta.29.'), findsOneWidget);
    await tester.tap(find.text('Скачать'));
    await tester.pumpAndSettle();
    expect(native.installs, 1);
  });

  test(
    'a manual check of an older or equal manifest never offers a download',
    () async {
      for (final installed in [28, 29]) {
        final result = await checkForUpdateDetailed(
          FakeApi(envelope(manifest(code: 28))),
          FakeUpdateNative(installed),
        );
        expect(result.status, UpdateCheckStatus.upToDate);
        expect(result.offer, isNull);
      }
    },
  );

  group('ADR-188 download progress', () {
    const mib = 1024 * 1024;

    test('progress snapshot parse is strict', () {
      final ok = UpdateProgress.parse({
        'phase': 'downloading',
        'received': 10,
        'total': 20,
        'outcome': '',
      })!;
      expect(ok.phase, UpdateNativePhase.downloading);
      expect(ok.received, 10);
      for (final bad in <Object?>[
        null,
        'downloading',
        {'phase': 'other', 'received': 1, 'total': 1, 'outcome': ''},
        {'phase': 'idle', 'received': -1, 'total': 1, 'outcome': ''},
        {'phase': 'idle', 'received': 1, 'total': '1', 'outcome': ''},
        {'phase': 'idle', 'received': 1, 'total': 1},
      ]) {
        expect(UpdateProgress.parse(bad), isNull, reason: '$bad');
      }
    });

    test('progress text shows sizes and a whole percent', () {
      expect(
        updateProgressText(12 * mib + mib ~/ 4, 46 * mib),
        '12,3 МБ из 46,0 МБ · 26 %',
      );
      expect(updateProgressText(0, 46 * mib), '0,0 МБ из 46,0 МБ · 0 %');
      expect(updateProgressText(99 * mib, 46 * mib), contains('100 %'));
      expect(updateProgressText(3 * mib, 0), 'Загружено 3,0 МБ');
    });

    Future<void> openWith(
      WidgetTester tester,
      UpdateOffer offer,
      UpdateDownload download,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => TextButton(
              onPressed: () => showUpdateDialog(
                context,
                offer,
                download.native,
                download: download,
              ),
              child: const Text('open'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
    }

    final optional = UpdateOffer(
      UpdateManifest.parse(manifest(size: 40 * mib))!,
      UpdateDecision.optional,
      signedOf(manifest(size: 40 * mib)),
    );

    testWidgets(
      'a running download shows bytes and percent, survives reopening, and can be cancelled',
      (tester) async {
        final native = SteppedUpdateNative();
        final download = UpdateDownload(
          native,
          pollEvery: const Duration(milliseconds: 50),
        );
        await openWith(tester, optional, download);
        await tester.tap(find.text('Скачать'));
        await tester.pump();
        native.snapshot = UpdateProgress(
          UpdateNativePhase.downloading,
          10 * mib,
          40 * mib,
          '',
        );
        await tester.pump(const Duration(milliseconds: 60));
        await tester.pump();
        expect(find.text('10,0 МБ из 40,0 МБ · 25 %'), findsOneWidget);
        final bar = tester.widget<LinearProgressIndicator>(
          find.byKey(const ValueKey('update-progress')),
        );
        expect(bar.value, closeTo(0.25, 1e-9));

        // Hide, then open again (the owner's repeated tap): same download, live progress.
        await tester.tap(find.text('Скрыть'));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 300));
        await tester.pump();
        expect(find.byKey(const ValueKey('update-progress')), findsNothing);
        native.snapshot = UpdateProgress(
          UpdateNativePhase.downloading,
          30 * mib,
          40 * mib,
          '',
        );
        await openWith(tester, optional, download);
        expect(find.text('30,0 МБ из 40,0 МБ · 75 %'), findsOneWidget);
        expect(find.text('Загрузка уже идёт.'), findsNothing);
        expect(native.installs, 1);

        await tester.tap(find.byKey(const ValueKey('update-cancel')));
        await tester.pump();
        await tester.pump();
        expect(native.cancels, 1);
        expect(find.text('Загрузка отменена.'), findsOneWidget);
        expect(find.text('Скачать'), findsOneWidget);
        download.dispose();
      },
    );

    testWidgets('verifying shows an indeterminate bar, then «Установить»', (
      tester,
    ) async {
      final native = SteppedUpdateNative();
      final download = UpdateDownload(
        native,
        pollEvery: const Duration(milliseconds: 50),
      );
      await openWith(tester, optional, download);
      await tester.tap(find.text('Скачать'));
      await tester.pump();
      native.snapshot = UpdateProgress(
        UpdateNativePhase.verifying,
        40 * mib,
        40 * mib,
        '',
      );
      await tester.pump(const Duration(milliseconds: 60));
      await tester.pump();
      expect(find.text('Проверка файла…'), findsOneWidget);
      expect(
        tester
            .widget<LinearProgressIndicator>(
              find.byKey(const ValueKey('update-progress')),
            )
            .value,
        isNull,
      );
      native.finish('installer_opened');
      await tester.pump();
      await tester.pump();
      expect(
        find.text('Подтвердите установку в системном окне Android.'),
        findsOneWidget,
      );
      await tester.tap(find.text('Установить'));
      await tester.pump();
      expect(native.installs, 2, reason: 'reopens the installer via native');
      native.finish('installer_opened');
      await tester.pump();
      download.dispose();
    });

    test('"busy" follows the download already running natively', () async {
      final native = SteppedUpdateNative(answerBusy: true);
      final download = UpdateDownload(
        native,
        pollEvery: const Duration(milliseconds: 1),
      );
      native.snapshot = const UpdateProgress(
        UpdateNativePhase.downloading,
        5,
        10,
        '',
      );
      final run = download.start(optional.signed, expectedTotal: 10);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(download.state.value.active, isTrue);
      native.snapshot = const UpdateProgress(
        UpdateNativePhase.ready,
        10,
        10,
        'verified',
      );
      await run;
      expect(download.state.value.outcome, 'verified');
      expect(download.state.value.installable, isTrue);
      download.dispose();
    });
  });
}

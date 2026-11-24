import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/app_update.dart';
import 'package:vpn_client/settings_screen.dart';
import 'package:vpn_client/update_ui.dart';

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

  /// The `openInstaller` flag of every install call, in order.
  final openFlags = <bool>[];
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
  Future<String> install(
    SignedManifest signed, {
    required bool openInstaller,
  }) async {
    installs++;
    openFlags.add(openInstaller);
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
  Future<String> install(SignedManifest signed, {required bool openInstaller}) {
    installs++;
    openFlags.add(openInstaller);
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

  /// Opens the ADR-193 update sheet over a button. Returns the download it shows.
  Future<UpdateDownload> open(
    WidgetTester tester,
    UpdateOffer offer,
    UpdateNative native, {
    UpdateDownload? download,
  }) async {
    final value =
        download ??
        UpdateDownload(native, pollEvery: const Duration(milliseconds: 50));
    addTearDown(() {
      if (download == null) value.dispose();
    });
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showUpdateSheet(context, offer, value),
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    return value;
  }

  UpdateOffer offerFor(UpdateDecision decision, [int size = 1024]) =>
      UpdateOffer(
        UpdateManifest.parse(manifest(size: size))!,
        decision,
        signedOf(manifest(size: size)),
      );

  testWidgets('blocking sheet has no "later" and survives back', (
    tester,
  ) async {
    final native = FakeUpdateNative(27, outcome: 'verified');
    await open(tester, offerFor(UpdateDecision.blocking), native);
    expect(find.text('Нужно обновить приложение'), findsOneWidget);
    expect(find.text('Позже'), findsNothing);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Нужно обновить приложение'), findsOneWidget);
    await tester.tap(find.text('Скачать'));
    await tester.pumpAndSettle();
    expect(native.installs, 1);
    expect(native.openFlags, [false], reason: 'download only, no installer');
    expect(
      find.text('Файл скачан и проверен. Можно устанавливать.'),
      findsOneWidget,
    );
    expect(find.text('Установить'), findsOneWidget);
  });

  testWidgets('optional sheet names the version, shows notes and closes', (
    tester,
  ) async {
    await open(tester, offerFor(UpdateDecision.optional), FakeUpdateNative(29));
    expect(find.text('Доступно обновление'), findsOneWidget);
    expect(find.text('Версия 0.1.0-beta.29 · 0,0 МБ'), findsOneWidget);
    expect(find.text('Исправления'), findsOneWidget);
    await tester.tap(find.text('Позже'));
    await tester.pumpAndSettle();
    expect(find.text('Доступно обновление'), findsNothing);
  });

  testWidgets(
    'a refused signature is reported and not offered for retry (optional)',
    (tester) async {
      final native = FakeUpdateNative(27, outcome: 'manifest_unverified');
      await open(tester, offerFor(UpdateDecision.optional), native);
      await tester.tap(find.text('Скачать'));
      await tester.pumpAndSettle();
      expect(
        find.text(
          'Не удалось проверить подпись обновления. Обновление не установлено.',
        ),
        findsOneWidget,
      );
      expect(find.text('Повторить загрузку'), findsNothing);
      expect(find.text('Установить'), findsNothing);
    },
  );

  for (final (code, message) in [
    (
      'hash_mismatch',
      'Контрольная сумма скачанного файла не совпала. Файл удалён, ничего не установлено.',
    ),
    (
      'size_mismatch',
      'Размер скачанного файла не совпал с ожидаемым. Файл удалён, ничего не установлено.',
    ),
    (
      'package_mismatch',
      'Скачанный файл не является нужной версией DetourVPN. Файл удалён, ничего не установлено.',
    ),
    (
      'download_failed',
      'Не удалось скачать обновление: нет связи с сервером или загрузка прервалась. Проверьте интернет и повторите загрузку.',
    ),
  ]) {
    testWidgets('$code is explained and the download can be retried', (
      tester,
    ) async {
      final native = FakeUpdateNative(27, outcome: code);
      await open(tester, offerFor(UpdateDecision.optional), native);
      await tester.tap(find.text('Скачать'));
      await tester.pumpAndSettle();
      expect(find.text(message), findsOneWidget);
      expect(find.text('Установить'), findsNothing);
      await tester.tap(find.text('Повторить загрузку'));
      await tester.pumpAndSettle();
      expect(native.openFlags, [false, false], reason: 'retry downloads again');
    });
  }

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

    test('an open update sheet blocks a second one', () async {
      final scheduler = UpdateScheduler(
        FakeApi(envelope(manifest())),
        FakeUpdateNative(27), // blocking: an automatic check opens the sheet
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

  group('ADR-188 progress values', () {
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
  });

  group('ADR-193 download state machine', () {
    const mib = 1024 * 1024;
    final signed = signedOf(manifest(size: 40 * mib));

    /// Lets the periodic poll (1 ms) read the current native snapshot.
    Future<void> tick() =>
        Future<void>.delayed(const Duration(milliseconds: 20));

    UpdateDownload downloadOf(UpdateNative native) {
      final value = UpdateDownload(
        native,
        pollEvery: const Duration(milliseconds: 1),
      );
      addTearDown(value.dispose);
      return value;
    }

    test(
      'idle → downloading with real byte counts → verifying → ready',
      () async {
        final native = SteppedUpdateNative();
        final download = downloadOf(native);
        final seen = <UpdateStage>[];
        download.state.addListener(() => seen.add(download.state.value.stage));
        expect(download.state.value.stage, UpdateStage.idle);
        expect(download.state.value.installable, isFalse);

        final run = download.start(signed, expectedTotal: 40 * mib);
        expect(download.state.value.stage, UpdateStage.downloading);
        expect(download.state.value.total, 40 * mib);
        expect(download.state.value.fraction, 0);

        native.snapshot = UpdateProgress(
          UpdateNativePhase.downloading,
          10 * mib,
          40 * mib,
          '',
        );
        await tick();
        expect(download.state.value.received, 10 * mib);
        expect(download.state.value.fraction, closeTo(0.25, 1e-9));

        native.snapshot = UpdateProgress(
          UpdateNativePhase.verifying,
          40 * mib,
          40 * mib,
          '',
        );
        await tick();
        expect(download.state.value.stage, UpdateStage.verifying);
        expect(download.state.value.fraction, isNull);

        native.finish('verified');
        await run;
        expect(download.state.value.stage, UpdateStage.ready);
        expect(download.state.value.installable, isTrue);
        expect(download.state.value.received, 40 * mib);
        expect(native.openFlags, [
          false,
        ], reason: 'the installer is not opened');
        expect(seen.toSet(), {
          UpdateStage.downloading,
          UpdateStage.verifying,
          UpdateStage.ready,
        });
      },
    );

    test('ready → installing → ready with the installer opened', () async {
      final native = SteppedUpdateNative();
      final download = downloadOf(native);
      final first = download.start(signed, expectedTotal: 40 * mib);
      native.finish('verified');
      await first;

      final install = download.install(signed);
      expect(download.state.value.stage, UpdateStage.installing);
      expect(download.state.value.active, isTrue);
      native.finish('installer_opened');
      await install;
      expect(download.state.value.stage, UpdateStage.ready);
      expect(download.state.value.outcome, 'installer_opened');
      expect(native.openFlags, [false, true]);

      // Permission screen first: still ready, «Установить» again later.
      final again = download.install(signed);
      native.finish('install_permission_required');
      await again;
      expect(download.state.value.stage, UpdateStage.ready);
      expect(native.openFlags, [false, true, true]);
    });

    test('install is ignored unless a verified file is ready', () async {
      final native = SteppedUpdateNative();
      final download = downloadOf(native);
      await download.install(signed);
      expect(native.installs, 0);
      expect(download.state.value.stage, UpdateStage.idle);
    });

    test('a second start while downloading does nothing', () async {
      final native = SteppedUpdateNative();
      final download = downloadOf(native);
      final run = download.start(signed, expectedTotal: 40 * mib);
      await download.start(signed, expectedTotal: 40 * mib);
      expect(native.installs, 1);
      native.finish('verified');
      await run;
    });

    test('cancel → cancelled, retryable, no installer', () async {
      final native = SteppedUpdateNative();
      final download = downloadOf(native);
      final run = download.start(signed, expectedTotal: 40 * mib);
      native.snapshot = UpdateProgress(
        UpdateNativePhase.downloading,
        5 * mib,
        40 * mib,
        '',
      );
      await tick();
      await download.cancel();
      await run;
      expect(native.cancels, 1);
      expect(download.state.value.stage, UpdateStage.cancelled);
      expect(download.state.value.installable, isFalse);
      expect(download.state.value.retryable, isTrue);
      expect(native.openFlags, [false]);
    });

    test('cancel is not offered while the file is verified', () async {
      final native = SteppedUpdateNative();
      final download = downloadOf(native);
      final run = download.start(signed, expectedTotal: 40 * mib);
      native.snapshot = UpdateProgress(
        UpdateNativePhase.verifying,
        40 * mib,
        40 * mib,
        '',
      );
      await tick();
      await download.cancel();
      expect(native.cancels, 0);
      native.finish('verified');
      await run;
    });

    test(
      'failures end in failed; only some are worth a new download',
      () async {
        for (final (code, retry) in [
          ('download_failed', true),
          ('size_mismatch', true),
          ('hash_mismatch', true),
          ('package_mismatch', true),
          ('manifest_unverified', false),
          ('invalid_manifest', false),
          ('not_newer', false),
        ]) {
          final download = downloadOf(FakeUpdateNative(27, outcome: code));
          await download.start(signed, expectedTotal: 40 * mib);
          final state = download.state.value;
          expect(state.stage, UpdateStage.failed, reason: code);
          expect(state.outcome, code);
          expect(state.installable, isFalse, reason: code);
          expect(state.retryable, retry, reason: code);
        }
      },
    );

    test('a hand-off that fails verification again is a failure', () async {
      final native = SteppedUpdateNative();
      final download = downloadOf(native);
      final first = download.start(signed, expectedTotal: 40 * mib);
      native.finish('verified');
      await first;
      final install = download.install(signed);
      native.finish('hash_mismatch');
      await install;
      expect(download.state.value.stage, UpdateStage.failed);
      expect(download.state.value.installable, isFalse);
    });

    test('"busy" follows the download already running natively', () async {
      final native = SteppedUpdateNative(answerBusy: true);
      final download = downloadOf(native);
      native.snapshot = const UpdateProgress(
        UpdateNativePhase.downloading,
        5,
        10,
        '',
      );
      final run = download.start(signed, expectedTotal: 10);
      await tick();
      expect(download.state.value.active, isTrue);
      expect(download.state.value.received, 5);
      native.snapshot = const UpdateProgress(
        UpdateNativePhase.ready,
        10,
        10,
        'verified',
      );
      await run;
      expect(download.state.value.outcome, 'verified');
      expect(download.state.value.installable, isTrue);
    });
  });

  group('ADR-193 sheet with a stepped download', () {
    const mib = 1024 * 1024;
    final optional = UpdateOffer(
      UpdateManifest.parse(manifest(size: 40 * mib))!,
      UpdateDecision.optional,
      signedOf(manifest(size: 40 * mib)),
    );

    Future<void> frames(WidgetTester tester) async {
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(milliseconds: 100));
      }
    }

    testWidgets(
      'progress bar follows bytes, survives reopening, and cancel deletes nothing installable',
      (tester) async {
        final native = SteppedUpdateNative();
        final download = UpdateDownload(
          native,
          pollEvery: const Duration(milliseconds: 50),
        );
        addTearDown(download.dispose);
        await open(tester, optional, native, download: download);
        expect(find.text('Версия 0.1.0-beta.29 · 40,0 МБ'), findsOneWidget);
        await tester.tap(find.text('Скачать'));
        await tester.pump();
        native.snapshot = UpdateProgress(
          UpdateNativePhase.downloading,
          10 * mib,
          40 * mib,
          '',
        );
        await frames(tester);
        expect(find.text('10,0 МБ из 40,0 МБ · 25 %'), findsOneWidget);
        final bar = tester.widget<LinearProgressIndicator>(
          find.byKey(const ValueKey('update-progress')),
        );
        expect(bar.value, closeTo(0.25, 1e-9));
        expect(find.text('Установить'), findsNothing);

        // Hide, then open again: same download, live progress, no second native call.
        await tester.tap(find.text('Скрыть'));
        await frames(tester);
        expect(find.byKey(const ValueKey('update-progress')), findsNothing);
        native.snapshot = UpdateProgress(
          UpdateNativePhase.downloading,
          30 * mib,
          40 * mib,
          '',
        );
        await tester.tap(find.text('open'));
        await frames(tester);
        expect(find.text('30,0 МБ из 40,0 МБ · 75 %'), findsOneWidget);
        expect(native.installs, 1);

        await tester.tap(find.byKey(const ValueKey('update-cancel')));
        await frames(tester);
        expect(native.cancels, 1);
        expect(
          find.text('Загрузка отменена. Скачанная часть удалена.'),
          findsOneWidget,
        );
        expect(find.text('Установить'), findsNothing);
        expect(find.text('Скачать снова'), findsOneWidget);
        expect(native.openFlags, [false]);
      },
    );

    testWidgets(
      'verifying shows an indeterminate bar, then an explicit «Установить» opens the installer',
      (tester) async {
        final native = SteppedUpdateNative();
        final download = UpdateDownload(
          native,
          pollEvery: const Duration(milliseconds: 50),
        );
        addTearDown(download.dispose);
        await open(tester, optional, native, download: download);
        await tester.tap(find.text('Скачать'));
        await tester.pump();
        native.snapshot = UpdateProgress(
          UpdateNativePhase.verifying,
          40 * mib,
          40 * mib,
          '',
        );
        await frames(tester);
        expect(find.text('Проверка файла…'), findsOneWidget);
        expect(
          tester
              .widget<LinearProgressIndicator>(
                find.byKey(const ValueKey('update-progress')),
              )
              .value,
          isNull,
        );
        final cancel = tester.widget<OutlinedButton>(
          find.byKey(const ValueKey('update-cancel')),
        );
        expect(cancel.onPressed, isNull);

        native.finish('verified');
        await frames(tester);
        expect(native.openFlags, [false], reason: 'no installer by itself');
        expect(
          find.text('Файл скачан и проверен. Можно устанавливать.'),
          findsOneWidget,
        );
        await tester.tap(find.text('Установить'));
        await tester.pump();
        expect(find.text('Открываем установщик Android…'), findsOneWidget);
        native.finish('installer_opened');
        await frames(tester);
        expect(native.openFlags, [false, true]);
        expect(
          find.textContaining('Подтвердите установку в окне Android.'),
          findsOneWidget,
        );
        expect(find.text('Установить'), findsOneWidget);
      },
    );
  });

  group('ADR-193 automatic checks', () {
    test(
      'an optional build only raises the banner; a manual check opens the sheet',
      () async {
        final scheduler = UpdateScheduler(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(29),
        );
        var shown = 0;
        Future<void> show(UpdateOffer _) async => shown++;
        await scheduler.run(manual: false, show: show);
        expect(shown, 0);
        expect(scheduler.bannerOffer.value?.manifest.versionCode, 30);
        await scheduler.run(manual: true, show: show);
        expect(shown, 1);
      },
    );

    test(
      'a blocking build opens the sheet even from an automatic check',
      () async {
        final scheduler = UpdateScheduler(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(27),
        );
        var shown = 0;
        await scheduler.run(manual: false, show: (_) async => shown++);
        expect(shown, 1);
        expect(scheduler.bannerOffer.value?.decision, UpdateDecision.blocking);
      },
    );
  });
}

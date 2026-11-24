import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/app_update.dart';
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/update_ui.dart';

import 'app_update_test.dart'
    show FakeApi, FakeUpdateNative, SteppedUpdateNative, envelope, manifest;

UpdateOffer offerOf({int code = 30, int minimum = 28}) {
  final m = UpdateManifest.parse(manifest(code: code, minimum: minimum))!;
  return UpdateOffer(
    m,
    minimum > 29 ? UpdateDecision.blocking : UpdateDecision.optional,
    SignedManifest.parse(envelope(manifest(code: code, minimum: minimum)))!,
  );
}

void main() {
  group('nextBannerOffer', () {
    final offer = offerOf();
    test('a newer build shows the banner', () {
      expect(
        nextBannerOffer(null, UpdateCheck(UpdateCheckStatus.available, offer)),
        same(offer),
      );
    });
    test('up to date hides it', () {
      expect(
        nextBannerOffer(offer, const UpdateCheck(UpdateCheckStatus.upToDate)),
        isNull,
      );
    });
    test('a failed check keeps what was known', () {
      expect(
        nextBannerOffer(offer, const UpdateCheck(UpdateCheckStatus.failed)),
        same(offer),
      );
      expect(
        nextBannerOffer(null, const UpdateCheck(UpdateCheckStatus.failed)),
        isNull,
      );
    });
    test('a hidden optional build stays hidden; blocking or newer shows', () {
      expect(
        nextBannerOffer(
          null,
          UpdateCheck(UpdateCheckStatus.available, offer),
          dismissedCode: 30,
        ),
        isNull,
      );
      final blocking = offerOf(minimum: 30);
      expect(
        nextBannerOffer(
          null,
          UpdateCheck(UpdateCheckStatus.available, blocking),
          dismissedCode: 30,
        ),
        same(blocking),
      );
      final newer = offerOf(code: 31);
      expect(
        nextBannerOffer(
          null,
          UpdateCheck(UpdateCheckStatus.available, newer),
          dismissedCode: 30,
        ),
        same(newer),
      );
    });
  });

  test('banner text names the version and a required update', () {
    expect(updateBannerText(offerOf()), 'Доступна новая версия 0.1.0-beta.29');
    expect(
      updateBannerText(offerOf(minimum: 30)),
      'Нужно обновить приложение до версии 0.1.0-beta.29',
    );
  });

  test('banner detail follows the download stage', () {
    final offer = offerOf();
    expect(
      updateBannerDetail(offer, const UpdateDownloadState()),
      startsWith('Размер 0,0 МБ'),
    );
    expect(
      updateBannerDetail(
        offer,
        const UpdateDownloadState(
          stage: UpdateStage.downloading,
          received: 512 * 1024,
          total: 1024 * 1024,
        ),
      ),
      '0,5 МБ из 1,0 МБ · 50 %',
    );
    expect(
      updateBannerDetail(
        offer,
        const UpdateDownloadState(stage: UpdateStage.ready),
      ),
      'Файл скачан и проверен. Готово к установке',
    );
  });

  group('UpdateScheduler banner', () {
    test(
      'is null before any check and set after a newer build is found',
      () async {
        final scheduler = UpdateScheduler(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(29),
        );
        expect(scheduler.bannerOffer.value, isNull);
        await scheduler.run(manual: false, show: (_) async {});
        expect(scheduler.bannerOffer.value?.manifest.versionCode, 30);
      },
    );
    test('stays null when the installed build is current', () async {
      final scheduler = UpdateScheduler(
        FakeApi(envelope(manifest())),
        FakeUpdateNative(30),
      );
      await scheduler.run(manual: false, show: (_) async {});
      expect(scheduler.bannerOffer.value, isNull);
    });
    test('hiding an optional build lasts for the session', () async {
      final scheduler = UpdateScheduler(
        FakeApi(envelope(manifest())),
        FakeUpdateNative(29),
      );
      await scheduler.run(manual: false, show: (_) async {});
      scheduler.dismissBanner();
      expect(scheduler.bannerOffer.value, isNull);
      await scheduler.run(manual: true, show: (_) async {});
      expect(scheduler.bannerOffer.value, isNull);
    });
    test('a blocking build cannot be hidden', () async {
      final scheduler = UpdateScheduler(
        FakeApi(envelope(manifest())),
        FakeUpdateNative(27),
      );
      await scheduler.run(manual: false, show: (_) async {});
      scheduler.dismissBanner();
      expect(scheduler.bannerOffer.value?.decision, UpdateDecision.blocking);
    });
    test(
      'present opens the sheet once and not while another is open',
      () async {
        final scheduler = UpdateScheduler(
          FakeApi(envelope(manifest())),
          FakeUpdateNative(29),
        );
        var shown = 0;
        await scheduler.present(offerOf(), (_) async => shown++);
        expect(shown, 1);
        final first = scheduler.run(
          manual: true,
          show: (_) => Future<void>.delayed(const Duration(milliseconds: 10)),
        );
        await scheduler.present(offerOf(), (_) async => shown++);
        await first;
        expect(shown, 1);
      },
    );
  });

  Future<
    ({
      ValueNotifier<UpdateOffer?> offer,
      UpdateDownload download,
      List<UpdateOffer> opened,
      List<int> dismissed,
    })
  >
  pumpSlot(WidgetTester tester, UpdateDownload download) async {
    final offer = ValueNotifier<UpdateOffer?>(null);
    addTearDown(offer.dispose);
    final opened = <UpdateOffer>[];
    final dismissed = <int>[];
    await tester.pumpWidget(
      MaterialApp(
        theme: orbitTheme(Brightness.dark),
        home: Scaffold(
          body: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              UpdateBannerSlot(
                offer: offer,
                download: download.state,
                onOpen: opened.add,
                onDismiss: () {
                  dismissed.add(1);
                  offer.value = null;
                },
              ),
              const Expanded(child: Center(child: Text('content'))),
            ],
          ),
        ),
      ),
    );
    await tester.pump();
    return (
      offer: offer,
      download: download,
      opened: opened,
      dismissed: dismissed,
    );
  }

  testWidgets('banner: hidden before a check', (tester) async {
    final download = UpdateDownload(FakeUpdateNative(29));
    addTearDown(download.dispose);
    await pumpSlot(tester, download);
    expect(find.byType(UpdateBanner), findsNothing);
    expect(find.text('content'), findsOneWidget);
  });

  testWidgets(
    'banner: slides in for a newer build, opens the sheet, and can be hidden',
    (tester) async {
      final download = UpdateDownload(FakeUpdateNative(29));
      addTearDown(download.dispose);
      final slot = await pumpSlot(tester, download);
      slot.offer.value = offerOf();
      await tester.pump();
      // Mid-animation: the banner is entering from above.
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.byType(SlideTransition), findsWidgets);
      await tester.pumpAndSettle();
      expect(find.text('Доступна новая версия 0.1.0-beta.29'), findsOneWidget);
      expect(find.text('Исправления'), findsNothing); // notes live in the sheet

      await tester.tap(find.byKey(const ValueKey('update-banner-open')));
      expect(slot.opened.single.manifest.versionCode, 30);
      await tester.tap(find.text('Доступна новая версия 0.1.0-beta.29'));
      expect(slot.opened, hasLength(2), reason: 'the whole banner is a target');

      await tester.tap(find.byKey(const ValueKey('update-banner-dismiss')));
      await tester.pumpAndSettle();
      expect(slot.dismissed, hasLength(1));
      expect(find.byType(UpdateBanner), findsNothing);
    },
  );

  testWidgets('banner: a blocking build has no close button', (tester) async {
    final download = UpdateDownload(FakeUpdateNative(27));
    addTearDown(download.dispose);
    final slot = await pumpSlot(tester, download);
    slot.offer.value = offerOf(minimum: 30);
    await tester.pumpAndSettle();
    expect(
      find.text('Нужно обновить приложение до версии 0.1.0-beta.29'),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('update-banner-dismiss')), findsNothing);
  });

  testWidgets('banner: shows live download progress and the ready state', (
    tester,
  ) async {
    const mib = 1024 * 1024;
    final native = SteppedUpdateNative();
    final download = UpdateDownload(
      native,
      pollEvery: const Duration(milliseconds: 50),
    );
    addTearDown(download.dispose);
    final slot = await pumpSlot(tester, download);
    slot.offer.value = offerOf();
    await tester.pumpAndSettle();
    expect(find.text('Обновить'), findsOneWidget);

    final run = download.start(
      slot.offer.value!.signed,
      expectedTotal: 4 * mib,
    );
    native.snapshot = const UpdateProgress(
      UpdateNativePhase.downloading,
      mib,
      4 * mib,
      '',
    );
    await tester.pump(const Duration(milliseconds: 120));
    await tester.pump();
    expect(find.text('1,0 МБ из 4,0 МБ · 25 %'), findsOneWidget);
    expect(
      tester
          .widget<LinearProgressIndicator>(
            find.byKey(const ValueKey('update-banner-progress')),
          )
          .value,
      closeTo(0.25, 1e-9),
    );
    expect(find.text('Открыть'), findsOneWidget);

    native.finish('verified');
    await run;
    await tester.pump();
    expect(
      find.text('Файл скачан и проверен. Готово к установке'),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('update-banner-progress')), findsNothing);
  });
}

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/app_update.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/home_screen.dart';
import 'package:vpn_client/orbit_theme.dart';

import 'app_update_test.dart'
    show FakeApi, FakeUpdateNative, envelope, manifest;
import 'connection_test.dart' as conn show FakeApi, FakeNative;

UpdateOffer offerOf({int code = 30, int minimum = 28}) {
  final m = UpdateManifest.parse(manifest(code: code, minimum: minimum))!;
  return UpdateOffer(
    m,
    minimum > 29 ? UpdateDecision.blocking : UpdateDecision.optional,
    SignedManifest.parse(envelope(manifest()))!,
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
  });

  test('banner text names the version and a required update', () {
    expect(updateBannerText(offerOf()), 'Доступна новая версия 0.1.0-beta.29');
    expect(
      updateBannerText(offerOf(minimum: 30)),
      'Нужно обновить приложение до версии 0.1.0-beta.29',
    );
  });

  group('UpdateScheduler.bannerOffer', () {
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
    test(
      'present opens the dialog once and not while another is open',
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

  Future<void> pumpHome(
    WidgetTester tester, {
    ValueListenable<UpdateOffer?>? offer,
    void Function(UpdateOffer)? onOpen,
  }) async {
    final c = ConnectionController(conn.FakeApi(), conn.FakeNative());
    addTearDown(c.dispose);
    await tester.pumpWidget(
      MaterialApp(
        theme: orbitTheme(Brightness.dark),
        home: Scaffold(
          body: ConnectionScreen(
            controller: c,
            onSignOut: () async {},
            updateOffer: offer,
            onOpenUpdate: onOpen,
          ),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 1));
  }

  testWidgets('home banner: hidden without an updater', (tester) async {
    await pumpHome(tester);
    expect(find.byType(UpdateBanner), findsNothing);
  });

  testWidgets('home banner: hidden before a check and when up to date', (
    tester,
  ) async {
    final offer = ValueNotifier<UpdateOffer?>(null);
    addTearDown(offer.dispose);
    await pumpHome(tester, offer: offer, onOpen: (_) {});
    expect(find.byType(UpdateBanner), findsNothing);
  });

  testWidgets(
    'home banner: shown for a newer build and opens the dialog flow',
    (tester) async {
      final offer = ValueNotifier<UpdateOffer?>(null);
      addTearDown(offer.dispose);
      UpdateOffer? opened;
      await pumpHome(tester, offer: offer, onOpen: (o) => opened = o);
      offer.value = offerOf();
      await tester.pump();
      expect(find.text('Доступна новая версия 0.1.0-beta.29'), findsOneWidget);
      await tester.tap(find.text('Обновить'));
      expect(opened, same(offer.value));
      offer.value = null;
      await tester.pump();
      expect(find.byType(UpdateBanner), findsNothing);
    },
  );
}

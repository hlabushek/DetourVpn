import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/app_update.dart';

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
  FakeUpdateNative(this.installed, {this.outcome = 'installer_opened'});
  final int? installed;
  final String outcome;
  int installs = 0;
  @override
  Future<int?> installedVersionCode() async => installed;
  @override
  Future<String> install(UpdateManifest manifest) async {
    installs++;
    return outcome;
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
    test('asks the public manifest endpoint', () async {
      final api = FakeApi(manifest());
      final offer = await checkForUpdate(api, FakeUpdateNative(27));
      expect(api.paths, ['GET /api/v1/app/update-manifest']);
      expect(offer!.decision, UpdateDecision.blocking);
    });
    test(
      'unavailable manifest, unknown version or downgrade gives nothing',
      () async {
        expect(
          await checkForUpdate(
            FakeApi(manifest(), error: 'update_unavailable'),
            FakeUpdateNative(27),
          ),
          isNull,
        );
        expect(
          await checkForUpdate(FakeApi(manifest()), FakeUpdateNative(null)),
          isNull,
        );
        expect(
          await checkForUpdate(FakeApi(manifest()), FakeUpdateNative(40)),
          isNull,
        );
        expect(
          await checkForUpdate(
            FakeApi(manifest(sha: 'x')),
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
    );
    await open(tester, offer, native);
    expect(find.text('Нужно обновить приложение'), findsOneWidget);
    expect(find.text('Позже'), findsNothing);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Нужно обновить приложение'), findsOneWidget);
    await tester.tap(find.text('Обновить'));
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
    );
    await open(tester, offer, FakeUpdateNative(29));
    expect(find.text('Доступно обновление'), findsOneWidget);
    await tester.tap(find.text('Позже'));
    await tester.pumpAndSettle();
    expect(find.text('Доступно обновление'), findsNothing);
  });

  testWidgets('verification failure is reported in Russian', (tester) async {
    final native = FakeUpdateNative(27, outcome: 'hash_mismatch');
    final offer = UpdateOffer(
      UpdateManifest.parse(manifest())!,
      UpdateDecision.blocking,
    );
    await open(tester, offer, native);
    await tester.tap(find.text('Обновить'));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Загруженный файл не прошёл проверку и удалён. Попробуйте позже.',
      ),
      findsOneWidget,
    );
  });
}

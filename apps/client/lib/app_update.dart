import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'api.dart';

/// ADR-107: in-app update. The manifest comes from our API; the native side downloads over
/// HTTPS, checks size, SHA-256, package name and version, and opens the system installer.
enum UpdateDecision { none, optional, blocking }

class UpdateManifest {
  const UpdateManifest({
    required this.versionCode,
    required this.versionName,
    required this.minSupportedVersionCode,
    required this.apkUrl,
    required this.apkSha256,
    required this.apkSizeBytes,
    required this.releaseNotesRu,
  });

  final int versionCode;
  final String versionName;
  final int minSupportedVersionCode;
  final String apkUrl;
  final String apkSha256;
  final int apkSizeBytes;
  final String releaseNotesRu;

  static final _sha256 = RegExp(r'^[0-9a-f]{64}$');
  static final _name = RegExp(r'^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$');
  static const _maxCode = 2147483647;

  /// Strict parse; any missing, extra-typed or out-of-range value means "no update".
  static UpdateManifest? parse(Map<String, dynamic> value) {
    final code = value['version_code'];
    final name = value['version_name'];
    final minimum = value['min_supported_version_code'];
    final url = value['apk_url'];
    final sha = value['apk_sha256'];
    final size = value['apk_size_bytes'];
    final notes = value['release_notes_ru'];
    if (code is! int ||
        minimum is! int ||
        size is! int ||
        name is! String ||
        url is! String ||
        sha is! String ||
        notes is! String) {
      return null;
    }
    if (code < 1 ||
        code > _maxCode ||
        minimum < 1 ||
        minimum > code ||
        size < 1 ||
        size > 512 * 1024 * 1024 ||
        !_name.hasMatch(name) ||
        !_sha256.hasMatch(sha) ||
        notes.length > 2000 ||
        !isAcceptableUrl(url)) {
      return null;
    }
    return UpdateManifest(
      versionCode: code,
      versionName: name,
      minSupportedVersionCode: minimum,
      apkUrl: url,
      apkSha256: sha,
      apkSizeBytes: size,
      releaseNotesRu: notes,
    );
  }

  static bool isAcceptableUrl(String url) {
    if (url.length > 512 || url.contains(RegExp(r'\s'))) return false;
    final uri = Uri.tryParse(url);
    return uri != null &&
        uri.scheme == 'https' &&
        uri.host.isNotEmpty &&
        uri.userInfo.isEmpty &&
        !uri.hasFragment;
  }
}

/// Same rule as UpdatePolicy.decide (Java): never a downgrade or same-version update.
UpdateDecision decideUpdate(int installed, UpdateManifest manifest) {
  if (manifest.versionCode <= installed) return UpdateDecision.none;
  return installed < manifest.minSupportedVersionCode
      ? UpdateDecision.blocking
      : UpdateDecision.optional;
}

/// ADR-107 decision 5: what the API serves. The app trusts only what the native side verified
/// with the Ed25519 public key compiled into it; this class just carries the three strings.
class SignedManifest {
  const SignedManifest(this.payload, this.signature, this.keyId);
  final String payload;
  final String signature;
  final String keyId;

  static SignedManifest? parse(Map<String, dynamic> value) {
    final payload = value['payload'];
    final signature = value['signature'];
    final keyId = value['key_id'];
    if (value.length != 3 ||
        payload is! String ||
        signature is! String ||
        keyId is! String ||
        payload.isEmpty ||
        payload.length > 16 * 1024 ||
        signature.length != 88 ||
        !RegExp(r'^[0-9a-f]{16}$').hasMatch(keyId)) {
      return null;
    }
    return SignedManifest(payload, signature, keyId);
  }

  Map<String, Object> get channelArgs => {
    'payload': payload,
    'signature': signature,
    'keyId': keyId,
  };
}

abstract interface class UpdateNative {
  Future<int?> installedVersionCode();

  /// The installed `versionName` (for example `0.1.0-beta.31`), shown to the user. Null if unknown.
  Future<String?> installedVersionName();

  /// The verified manifest JSON text, or `null` when the signature does not verify (also on
  /// Android below 13, which has no platform Ed25519, and while no key is configured).
  Future<String?> verifyManifest(SignedManifest signed);

  /// The native side re-verifies [signed] and takes every install value from it. With
  /// [openInstaller] false it only downloads and verifies (answer `verified`); with true it
  /// re-verifies a file it already verified (or downloads anew) and opens the system installer.
  Future<String> install(SignedManifest signed, {required bool openInstaller});

  /// ADR-188: where the native download is now; null when the platform did not answer.
  Future<UpdateProgress?> progress();

  /// ADR-188: asks the running download to stop; its [install] call then answers "cancelled".
  Future<void> cancel();
}

/// ADR-188: the native download phase. `ready` means a verified file is waiting to be installed.
enum UpdateNativePhase { idle, downloading, verifying, ready }

/// ADR-188: one native progress snapshot. Byte counts only; no URL, hash or path crosses over.
class UpdateProgress {
  const UpdateProgress(this.phase, this.received, this.total, this.outcome);
  final UpdateNativePhase phase;
  final int received;
  final int total;

  /// Outcome code of the last finished native run, '' before the first one.
  final String outcome;

  static UpdateProgress? parse(Object? value) {
    if (value is! Map) return null;
    final phase = switch (value['phase']) {
      'idle' => UpdateNativePhase.idle,
      'downloading' => UpdateNativePhase.downloading,
      'verifying' => UpdateNativePhase.verifying,
      'ready' => UpdateNativePhase.ready,
      _ => null,
    };
    final received = value['received'];
    final total = value['total'];
    final outcome = value['outcome'];
    if (phase == null ||
        received is! int ||
        total is! int ||
        outcome is! String ||
        received < 0 ||
        total < 0 ||
        outcome.length > 64) {
      return null;
    }
    return UpdateProgress(phase, received, total, outcome);
  }
}

class PlatformUpdateNative implements UpdateNative {
  const PlatformUpdateNative();
  static const _channel = MethodChannel('project.vpn/tunnel.v1');

  @override
  Future<int?> installedVersionCode() async {
    try {
      final value = await _channel.invokeMethod<Object>('appVersionCode');
      return value is int ? value : null;
    } catch (_) {
      return null;
    }
  }

  @override
  Future<String?> installedVersionName() async {
    try {
      final value = await _channel.invokeMethod<Object>('appVersionName');
      return value is String && value.isNotEmpty ? value : null;
    } catch (_) {
      return null;
    }
  }

  @override
  Future<String?> verifyManifest(SignedManifest signed) async {
    try {
      final value = await _channel.invokeMethod<Object>(
        'appUpdateVerify',
        signed.channelArgs,
      );
      return value is String ? value : null;
    } catch (_) {
      return null;
    }
  }

  @override
  Future<String> install(
    SignedManifest signed, {
    required bool openInstaller,
  }) async {
    try {
      final value = await _channel.invokeMethod<Object>('appUpdateInstall', {
        ...signed.channelArgs,
        'openInstaller': openInstaller,
      });
      return value is String ? value : 'download_failed';
    } catch (_) {
      return 'download_failed';
    }
  }

  @override
  Future<UpdateProgress?> progress() async {
    try {
      return UpdateProgress.parse(
        await _channel.invokeMethod<Object>('appUpdateProgress'),
      );
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> cancel() async {
    try {
      await _channel.invokeMethod<Object>('appUpdateCancel');
    } catch (_) {
      // Nothing to stop, or an older native side: the download finishes on its own.
    }
  }
}

/// ADR-193: where the one update download is. `offered` is not a stage of the download: it is
/// [UpdateScheduler.bannerOffer] holding an offer while the download is still [idle].
enum UpdateStage {
  /// Nothing started (or a finished check offered nothing to download yet).
  idle,

  /// Bytes are arriving; [UpdateDownloadState.received] counts them.
  downloading,

  /// The whole file arrived; native checks size, SHA-256, package name and version.
  verifying,

  /// The file passed every check. Only «Установить» is left.
  ready,

  /// «Установить» was tapped; native re-verifies the file and opens the system installer.
  installing,

  /// The user stopped the download; native deleted the partial file.
  cancelled,

  /// The download or a check failed; native deleted the file. [UpdateDownloadState.outcome]
  /// says why.
  failed,
}

class UpdateDownloadState {
  const UpdateDownloadState({
    this.stage = UpdateStage.idle,
    this.received = 0,
    this.total = 0,
    this.outcome,
    this.cancelling = false,
  });
  final UpdateStage stage;
  final int received;
  final int total;

  /// Native outcome code of the last finished step (download or installer hand-off).
  final String? outcome;
  final bool cancelling;

  /// A native call is running; a second one must not start.
  bool get active =>
      stage == UpdateStage.downloading ||
      stage == UpdateStage.verifying ||
      stage == UpdateStage.installing;

  /// 0..1 while bytes are counted against a known size; null when not determinate.
  double? get fraction {
    if (stage != UpdateStage.downloading || total <= 0) return null;
    return (received / total).clamp(0.0, 1.0);
  }

  /// A verified file is waiting for «Установить».
  bool get installable => stage == UpdateStage.ready;

  /// Downloading again can help (network, a corrupted transfer). A refused signature, invalid
  /// data or a build that is not newer will fail the same way, so no «Повторить» is offered.
  bool get retryable =>
      stage == UpdateStage.cancelled ||
      (stage == UpdateStage.failed && !_finalFailures.contains(outcome));

  static const _finalFailures = {
    'manifest_unverified',
    'invalid_manifest',
    'not_newer',
  };

  /// Native answers after which a verified file is still in place ([UpdateStage.ready]).
  static const readyOutcomes = {
    'verified',
    'installer_opened',
    'installer_unavailable',
    'install_permission_required',
  };

  /// The stage a finished native call leads to.
  static UpdateStage stageAfter(String outcome) => switch (outcome) {
    _ when readyOutcomes.contains(outcome) => UpdateStage.ready,
    'cancelled' => UpdateStage.cancelled,
    _ => UpdateStage.failed,
  };
}

/// ADR-188/193: the one update download of this app. It outlives the sheet, so closing and
/// reopening it (or tapping the banner again) shows the running download instead of starting a
/// second one. Progress comes from polling native byte counts (never a timer that invents
/// numbers); the native call's own answer is the final outcome.
class UpdateDownload {
  UpdateDownload(
    this.native, {
    this.pollEvery = const Duration(milliseconds: 300),
  });
  final UpdateNative native;
  final Duration pollEvery;
  final ValueNotifier<UpdateDownloadState> state = ValueNotifier(
    const UpdateDownloadState(),
  );
  Timer? _poll;
  bool _polling = false;
  bool _disposed = false;

  /// Downloads and verifies only; the installer is not opened. Ends in [UpdateStage.ready],
  /// [UpdateStage.cancelled] or [UpdateStage.failed]. Ignored while a call is running.
  Future<void> start(SignedManifest signed, {required int expectedTotal}) =>
      _run(
        signed,
        openInstaller: false,
        first: UpdateDownloadState(
          stage: UpdateStage.downloading,
          total: expectedTotal,
        ),
      );

  /// Hands the verified file to the system installer (native re-verifies it first). Only from
  /// [UpdateStage.ready]; anything else is ignored.
  Future<void> install(SignedManifest signed) {
    final now = state.value;
    if (now.stage != UpdateStage.ready) return Future.value();
    return _run(
      signed,
      openInstaller: true,
      first: UpdateDownloadState(
        stage: UpdateStage.installing,
        received: now.received,
        total: now.total,
      ),
    );
  }

  Future<void> _run(
    SignedManifest signed, {
    required bool openInstaller,
    required UpdateDownloadState first,
  }) async {
    if (state.value.active || _disposed) return;
    _set(first);
    _poll = Timer.periodic(pollEvery, (_) => unawaited(_pollOnce()));
    var outcome = 'download_failed';
    try {
      outcome = await native.install(signed, openInstaller: openInstaller);
      // An earlier screen of this app (a recreated activity) still owns the native download:
      // follow it to its end instead of reporting a dead "already downloading".
      if (outcome == 'busy') outcome = await _follow();
    } catch (_) {
      outcome = 'download_failed';
    } finally {
      _poll?.cancel();
      _poll = null;
    }
    final last = state.value;
    final stage = UpdateDownloadState.stageAfter(outcome);
    _set(
      UpdateDownloadState(
        stage: stage,
        // A verified file is whole; a cancelled or failed one is gone.
        received: stage == UpdateStage.ready ? last.total : last.received,
        total: last.total,
        outcome: outcome,
      ),
    );
  }

  /// Stops a running download. The native call then answers `cancelled` and the partial file is
  /// deleted there. Not offered while the file is checked or the installer opens.
  Future<void> cancel() async {
    final now = state.value;
    if (now.stage != UpdateStage.downloading || now.cancelling || _disposed) {
      return;
    }
    _set(
      UpdateDownloadState(
        stage: now.stage,
        received: now.received,
        total: now.total,
        cancelling: true,
      ),
    );
    await native.cancel();
  }

  Future<void> _pollOnce() async {
    if (_polling || _disposed) return;
    _polling = true;
    try {
      final snapshot = await native.progress();
      final now = state.value;
      if (snapshot == null || !now.active || _disposed) return;
      final stage = switch (snapshot.phase) {
        UpdateNativePhase.verifying => UpdateStage.verifying,
        // During «Установить» native downloads again only if the cached file was lost.
        UpdateNativePhase.downloading => UpdateStage.downloading,
        // Finished natively; the native call answers in a moment.
        _ => now.stage,
      };
      _set(
        UpdateDownloadState(
          stage: stage,
          received: snapshot.received,
          total: snapshot.total > 0 ? snapshot.total : now.total,
          cancelling: now.cancelling,
        ),
      );
    } finally {
      _polling = false;
    }
  }

  /// Waits for a download this call did not start; its last native outcome is the answer.
  Future<String> _follow() async {
    var misses = 0;
    while (!_disposed) {
      await Future<void>.delayed(pollEvery);
      final snapshot = await native.progress();
      if (snapshot == null) {
        if (++misses >= 10) return 'download_failed';
        continue;
      }
      misses = 0;
      // Still running: the periodic poll keeps the shown progress current.
      if (snapshot.phase == UpdateNativePhase.downloading ||
          snapshot.phase == UpdateNativePhase.verifying) {
        continue;
      }
      return snapshot.outcome.isEmpty ? 'download_failed' : snapshot.outcome;
    }
    return 'download_failed';
  }

  void _set(UpdateDownloadState value) {
    if (!_disposed) state.value = value;
  }

  void dispose() {
    _disposed = true;
    _poll?.cancel();
    state.dispose();
  }
}

/// "12,3 МБ" (MiB, one decimal, Russian decimal comma).
String formatUpdateMegabytes(int bytes) =>
    '${(bytes / (1024 * 1024)).toStringAsFixed(1).replaceAll('.', ',')} МБ';

/// "12,3 МБ из 45,6 МБ · 27 %"; without a known size, just the received amount.
String updateProgressText(int received, int total) {
  if (total <= 0) return 'Загружено ${formatUpdateMegabytes(received)}';
  final clamped = received.clamp(0, total);
  final percent = clamped * 100 ~/ total;
  return '${formatUpdateMegabytes(clamped)} из ${formatUpdateMegabytes(total)} · $percent %';
}

class UpdateOffer {
  const UpdateOffer(this.manifest, this.decision, this.signed);
  final UpdateManifest manifest;
  final UpdateDecision decision;
  final SignedManifest signed;
}

/// Any failure (no manifest, network, invalid data) means no offer.
Future<UpdateOffer?> checkForUpdate(TunnelApi api, UpdateNative native) async =>
    (await checkForUpdateDetailed(api, native)).offer;

/// Result of one check, so a manual check can say why nothing was offered.
enum UpdateCheckStatus { available, upToDate, failed }

class UpdateCheck {
  const UpdateCheck(this.status, [this.offer]);
  final UpdateCheckStatus status;
  final UpdateOffer? offer;
}

/// `upToDate`: no newer verified build is published. `failed`: the check itself did not work
/// (network, unverifiable manifest, Android below 13). Never throws.
Future<UpdateCheck> checkForUpdateDetailed(
  TunnelApi api,
  UpdateNative native,
) async {
  const failed = UpdateCheck(UpdateCheckStatus.failed);
  try {
    final installed = await native.installedVersionCode();
    if (installed == null || installed < 1) return failed;
    final Map<String, dynamic> raw;
    try {
      raw = await api.call('GET', '/api/v1/app/signed-update-manifest');
    } on ClientError catch (e) {
      // The server has no valid release published: nothing to offer, not a broken check.
      return e.code == 'update_unavailable' || e.code == 'not_found'
          ? const UpdateCheck(UpdateCheckStatus.upToDate)
          : failed;
    }
    final signed = SignedManifest.parse(raw);
    if (signed == null) return failed;
    final verified = await native.verifyManifest(signed);
    if (verified == null) return failed;
    final decoded = jsonDecode(verified);
    if (decoded is! Map<String, dynamic>) return failed;
    final manifest = UpdateManifest.parse(decoded);
    if (manifest == null) return failed;
    final decision = decideUpdate(installed, manifest);
    return decision == UpdateDecision.none
        ? const UpdateCheck(UpdateCheckStatus.upToDate)
        : UpdateCheck(
            UpdateCheckStatus.available,
            UpdateOffer(manifest, decision, signed),
          );
  } catch (_) {
    return failed;
  }
}

/// "0.1.0-beta.31 (код 32)", the name alone, or null when the platform gave nothing usable.
String? installedVersionLabel(String? name, int? code) {
  final cleanName = name?.trim();
  final hasName =
      cleanName != null &&
      cleanName.isNotEmpty &&
      cleanName.length <= 64 &&
      !cleanName.contains(RegExp(r'[\x00-\x1f]'));
  final hasCode = code != null && code > 0;
  if (hasName && hasCode) return '$cleanName (код $code)';
  if (hasName) return cleanName;
  if (hasCode) return 'код $code';
  return null;
}

/// Russian text for a manual check that offered nothing.
String updateCheckText(UpdateCheckStatus status) => switch (status) {
  UpdateCheckStatus.upToDate => 'Установлена последняя версия DetourVPN.',
  UpdateCheckStatus.failed =>
    'Не удалось проверить обновления. Проверьте интернет и попробуйте ещё раз. '
        'Обновление из приложения работает на Android 13 и новее.',
  UpdateCheckStatus.available => 'Доступно обновление.',
};

/// The offer the update banner shows after [result]. A newer build replaces it, "up to date"
/// clears it, and a failed check keeps the previous one (a failed check proves nothing).
/// ADR-193: an optional build the user hid this session ([dismissedCode]) stays hidden; a
/// blocking one is always shown.
UpdateOffer? nextBannerOffer(
  UpdateOffer? previous,
  UpdateCheck result, {
  int? dismissedCode,
}) => switch (result.status) {
  UpdateCheckStatus.available
      when result.offer?.decision == UpdateDecision.optional &&
          result.offer?.manifest.versionCode == dismissedCode =>
    null,
  UpdateCheckStatus.available => result.offer,
  UpdateCheckStatus.upToDate => null,
  UpdateCheckStatus.failed => previous,
};

/// Banner text: the version and whether the installed one is still supported.
String updateBannerText(UpdateOffer offer) =>
    offer.decision == UpdateDecision.blocking
    ? 'Нужно обновить приложение до версии ${offer.manifest.versionName}'
    : 'Доступна новая версия ${offer.manifest.versionName}';

/// When to check: at start, when the app returns to the screen (at most every [autoEvery]),
/// and whenever the user asks. One check at a time; no second sheet while one is open.
/// ADR-193: an automatic check announces an optional build only through the banner; the sheet
/// opens by itself only for a blocking build or a manual check.
class UpdateScheduler {
  UpdateScheduler(
    this.api,
    this.native, {
    this.autoEvery = const Duration(hours: 6),
    DateTime Function()? now,
  }) : _now = now ?? DateTime.now;
  final TunnelApi api;
  final UpdateNative native;
  final Duration autoEvery;
  final DateTime Function() _now;
  DateTime? _lastCheck;
  bool _running = false;
  bool _showing = false;

  /// The offer from the last check that worked; null until a check found a newer build, and
  /// after the user hid an optional one. Drives the in-app update banner.
  final ValueNotifier<UpdateOffer?> bannerOffer = ValueNotifier(null);

  /// ADR-188: the app-wide download every update sheet shows.
  late final UpdateDownload download = UpdateDownload(native);

  /// Version code of the optional build whose banner the user hid this session.
  int? _dismissedCode;

  /// ADR-193: hides the banner of an optional build until the app process ends. A blocking
  /// build cannot be hidden. A running download continues; Settings still reaches it.
  void dismissBanner() {
    final offer = bannerOffer.value;
    if (offer == null || offer.decision == UpdateDecision.blocking) return;
    _dismissedCode = offer.manifest.versionCode;
    bannerOffer.value = null;
  }

  bool get dueForAuto =>
      _lastCheck == null || _now().difference(_lastCheck!) >= autoEvery;

  /// Null when skipped (another check or sheet is in progress, or an automatic check is not
  /// due yet). [show] opens the sheet for an available update when [manual] or blocking.
  Future<UpdateCheck?> run({
    required bool manual,
    required Future<void> Function(UpdateOffer offer) show,
  }) async {
    if (_running || _showing || (!manual && !dueForAuto)) return null;
    _running = true;
    _lastCheck = _now();
    final UpdateCheck result;
    try {
      result = await checkForUpdateDetailed(api, native);
    } finally {
      _running = false;
    }
    bannerOffer.value = nextBannerOffer(
      bannerOffer.value,
      result,
      dismissedCode: _dismissedCode,
    );
    final offer = result.offer;
    if (offer != null &&
        (manual || offer.decision == UpdateDecision.blocking)) {
      _showing = true;
      try {
        await show(offer);
      } finally {
        _showing = false;
      }
    }
    return result;
  }

  /// Opens [show] for an offer already found (the update banner). Skipped while a check or
  /// another update sheet is in progress, like [run].
  Future<void> present(
    UpdateOffer offer,
    Future<void> Function(UpdateOffer offer) show,
  ) async {
    if (_running || _showing) return;
    _showing = true;
    try {
      await show(offer);
    } finally {
      _showing = false;
    }
  }
}

/// ADR-193: Russian text for a native outcome, accurate about what happened to the file.
String updateOutcomeText(String outcome) => switch (outcome) {
  'verified' => 'Файл скачан и проверен. Можно устанавливать.',
  'installer_opened' =>
    'Подтвердите установку в окне Android. Если окно не появилось или было закрыто, нажмите «Установить» ещё раз.',
  'install_permission_required' =>
    'Разрешите DetourVPN устанавливать приложения в открывшихся настройках, затем вернитесь и нажмите «Установить».',
  'installer_unavailable' =>
    'Не удалось открыть установщик Android. Нажмите «Установить» ещё раз.',
  'size_mismatch' =>
    'Размер скачанного файла не совпал с ожидаемым. Файл удалён, ничего не установлено.',
  'hash_mismatch' =>
    'Контрольная сумма скачанного файла не совпала. Файл удалён, ничего не установлено.',
  'package_mismatch' =>
    'Скачанный файл не является нужной версией DetourVPN. Файл удалён, ничего не установлено.',
  'manifest_unverified' =>
    'Не удалось проверить подпись обновления. Обновление не установлено.',
  'invalid_manifest' =>
    'Данные обновления на сервере некорректны. Обновление не установлено.',
  'not_newer' => 'У вас уже установлена актуальная версия.',
  'busy' => 'Загрузка уже идёт.',
  'cancelled' => 'Загрузка отменена. Скачанная часть удалена.',
  _ =>
    'Не удалось скачать обновление: нет связи с сервером или загрузка прервалась. Проверьте интернет и повторите загрузку.',
};

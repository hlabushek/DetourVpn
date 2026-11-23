import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
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

  /// The native side re-verifies [signed] and takes every install value from it.
  Future<String> install(SignedManifest signed);

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
  Future<String> install(SignedManifest signed) async {
    try {
      final value = await _channel.invokeMethod<Object>(
        'appUpdateInstall',
        signed.channelArgs,
      );
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

/// ADR-188: what the update dialog shows about the download.
enum UpdateFlow { idle, downloading, verifying, finished }

class UpdateDownloadState {
  const UpdateDownloadState({
    this.flow = UpdateFlow.idle,
    this.received = 0,
    this.total = 0,
    this.outcome,
    this.cancelling = false,
  });
  final UpdateFlow flow;
  final int received;
  final int total;

  /// Native outcome code once [flow] is `finished`.
  final String? outcome;
  final bool cancelling;

  bool get active =>
      flow == UpdateFlow.downloading || flow == UpdateFlow.verifying;

  /// 0..1 while bytes are counted against a known size; null when not determinate.
  double? get fraction {
    if (flow != UpdateFlow.downloading || total <= 0) return null;
    return (received / total).clamp(0.0, 1.0);
  }

  /// The file passed every check and only the system installer is left.
  bool get installable =>
      flow == UpdateFlow.finished && fileReadyOutcomes.contains(outcome);

  static const fileReadyOutcomes = {
    'installer_opened',
    'installer_unavailable',
    'verified',
  };
}

/// ADR-188: the one update download of this app. It outlives the dialog, so closing and reopening
/// the dialog (or tapping the banner again) shows the running download instead of starting a
/// second one. Progress comes from polling the native side; the install call's own answer is the
/// final outcome.
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

  /// Starts a download (or, for a verified file, opens the installer again). A call while one is
  /// running does nothing: the dialog already shows that download.
  Future<void> start(
    SignedManifest signed, {
    required int expectedTotal,
  }) async {
    if (state.value.active || _disposed) return;
    _set(
      UpdateDownloadState(flow: UpdateFlow.downloading, total: expectedTotal),
    );
    _poll = Timer.periodic(pollEvery, (_) => unawaited(_pollOnce()));
    var outcome = 'download_failed';
    try {
      outcome = await native.install(signed);
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
    _set(
      UpdateDownloadState(
        flow: UpdateFlow.finished,
        received: last.received,
        total: last.total,
        outcome: outcome,
      ),
    );
  }

  Future<void> cancel() async {
    final now = state.value;
    if (!now.active || now.cancelling || _disposed) return;
    _set(
      UpdateDownloadState(
        flow: now.flow,
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
      final flow = switch (snapshot.phase) {
        UpdateNativePhase.verifying => UpdateFlow.verifying,
        UpdateNativePhase.downloading => UpdateFlow.downloading,
        // Finished natively; the install call answers in a moment.
        _ => now.flow,
      };
      _set(
        UpdateDownloadState(
          flow: flow,
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

/// Any failure (no manifest, network, invalid data) means no dialog.
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

/// The offer the home-screen banner shows after [result]. A newer build replaces it, "up to date"
/// clears it, and a failed check keeps the previous one (a failed check proves nothing).
UpdateOffer? nextBannerOffer(UpdateOffer? previous, UpdateCheck result) =>
    switch (result.status) {
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
/// and whenever the user asks. One check at a time; no second dialog while one is open.
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

  /// The offer from the last check that worked; null until a check found a newer build.
  /// Drives the home-screen banner.
  final ValueNotifier<UpdateOffer?> bannerOffer = ValueNotifier(null);

  /// ADR-188: the app-wide download every update dialog shows.
  late final UpdateDownload download = UpdateDownload(native);

  bool get dueForAuto =>
      _lastCheck == null || _now().difference(_lastCheck!) >= autoEvery;

  /// Null when skipped (another check or dialog is in progress, or an automatic check is not
  /// due yet). [show] opens the dialog for an available update.
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
    bannerOffer.value = nextBannerOffer(bannerOffer.value, result);
    final offer = result.offer;
    if (offer != null) {
      _showing = true;
      try {
        await show(offer);
      } finally {
        _showing = false;
      }
    }
    return result;
  }

  /// Opens [show] for an offer already found (the home-screen banner). Skipped while a check or
  /// another update dialog is in progress, like [run].
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

String updateOutcomeText(String outcome) => switch (outcome) {
  'installer_opened' => 'Подтвердите установку в системном окне Android.',
  'install_permission_required' =>
    'Разрешите DetourVPN устанавливать приложения в открывшихся настройках, затем вернитесь и нажмите «Скачать» ещё раз.',
  'hash_mismatch' || 'size_mismatch' || 'package_mismatch' =>
    'Загруженный файл не прошёл проверку и удалён. Попробуйте позже.',
  'not_newer' => 'У вас уже установлена актуальная версия.',
  'manifest_unverified' =>
    'Не удалось проверить подпись обновления. Обновление не установлено.',
  'busy' => 'Загрузка уже идёт.',
  'cancelled' => 'Загрузка отменена.',
  'verified' => 'Обновление загружено и проверено. Нажмите «Установить».',
  'installer_unavailable' =>
    'Не удалось открыть установщик Android. Нажмите «Установить» ещё раз.',
  _ =>
    'Не удалось загрузить обновление. Проверьте интернет и попробуйте ещё раз.',
};

/// Blocking dialogs cannot be dismissed (no back, no tap outside, no "later"). [download] is the
/// app-wide download (ADR-188); without it the dialog gets its own, which lives only as long as
/// the dialog's caller keeps it.
Future<void> showUpdateDialog(
  BuildContext context,
  UpdateOffer offer,
  UpdateNative native, {
  UpdateDownload? download,
}) {
  final blocking = offer.decision == UpdateDecision.blocking;
  final owned = download == null ? UpdateDownload(native) : null;
  return showDialog<void>(
    context: context,
    barrierDismissible: !blocking,
    builder: (_) => PopScope(
      canPop: !blocking,
      child: _UpdateDialog(offer: offer, download: download ?? owned!),
    ),
  ).whenComplete(() {
    // An owned download is only dropped once it has ended; a running one keeps its native call.
    if (owned != null && !owned.state.value.active) owned.dispose();
  });
}

class _UpdateDialog extends StatelessWidget {
  const _UpdateDialog({required this.offer, required this.download});
  final UpdateOffer offer;
  final UpdateDownload download;

  void _start() => unawaited(
    download.start(offer.signed, expectedTotal: offer.manifest.apkSizeBytes),
  );

  @override
  Widget build(BuildContext context) {
    final blocking = offer.decision == UpdateDecision.blocking;
    final notes = offer.manifest.releaseNotesRu.trim();
    return ValueListenableBuilder<UpdateDownloadState>(
      valueListenable: download.state,
      builder: (context, state, _) => AlertDialog(
        title: Text(
          blocking ? 'Нужно обновить приложение' : 'Доступно обновление',
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                blocking
                    ? 'Эта версия DetourVPN больше не поддерживается. Установите версию ${offer.manifest.versionName}, чтобы продолжить.'
                    : 'Доступна версия ${offer.manifest.versionName}.',
              ),
              if (notes.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text(notes),
              ],
              if (state.flow != UpdateFlow.idle) ...[
                const SizedBox(height: 16),
                _UpdateProgressView(state: state, blocking: blocking),
              ],
            ],
          ),
        ),
        actions: state.active
            ? [
                if (!blocking)
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('Скрыть'),
                  ),
                TextButton(
                  key: const ValueKey('update-cancel'),
                  onPressed:
                      state.cancelling || state.flow != UpdateFlow.downloading
                      ? null
                      : () => unawaited(download.cancel()),
                  child: const Text('Отменить загрузку'),
                ),
              ]
            : [
                if (!blocking)
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('Позже'),
                  ),
                FilledButton(
                  onPressed: _start,
                  child: Text(state.installable ? 'Установить' : 'Скачать'),
                ),
              ],
      ),
    );
  }
}

/// ADR-188: a determinate bar with "12,3 МБ из 45,6 МБ · 27 %" while bytes arrive, an
/// indeterminate one while the file is checked, and the outcome text once it ended.
class _UpdateProgressView extends StatelessWidget {
  const _UpdateProgressView({required this.state, required this.blocking});
  final UpdateDownloadState state;
  final bool blocking;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodySmall;
    if (state.flow == UpdateFlow.finished) {
      final outcome = state.outcome ?? 'download_failed';
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            updateOutcomeText(outcome),
            key: const ValueKey('update-status'),
          ),
          if (outcome == 'installer_opened') ...[
            const SizedBox(height: 4),
            Text(
              'Если окно установки не появилось или было закрыто, нажмите «Установить».',
              style: muted,
            ),
          ],
        ],
      );
    }
    final verifying = state.flow == UpdateFlow.verifying;
    final fraction = state.fraction;
    final label = state.cancelling
        ? 'Отмена загрузки…'
        : verifying
        ? 'Проверка файла…'
        : updateProgressText(state.received, state.total);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        LinearProgressIndicator(
          key: const ValueKey('update-progress'),
          value: verifying ? null : (fraction ?? 0),
          semanticsLabel: 'Загрузка обновления',
          semanticsValue: fraction == null
              ? null
              : '${(fraction * 100).floor()} %',
        ),
        const SizedBox(height: 8),
        Text(label, key: const ValueKey('update-status')),
        if (!blocking && !state.cancelling) ...[
          const SizedBox(height: 4),
          Text(
            'Окно можно скрыть: загрузка продолжится, установщик откроется сам.',
            style: muted,
          ),
        ],
      ],
    );
  }
}

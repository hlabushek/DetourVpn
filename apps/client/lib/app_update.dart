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
  _ =>
    'Не удалось загрузить обновление. Проверьте интернет и попробуйте ещё раз.',
};

/// Blocking dialogs cannot be dismissed (no back, no tap outside, no "later").
Future<void> showUpdateDialog(
  BuildContext context,
  UpdateOffer offer,
  UpdateNative native,
) {
  final blocking = offer.decision == UpdateDecision.blocking;
  return showDialog<void>(
    context: context,
    barrierDismissible: !blocking,
    builder: (_) => PopScope(
      canPop: !blocking,
      child: _UpdateDialog(offer: offer, native: native),
    ),
  );
}

class _UpdateDialog extends StatefulWidget {
  const _UpdateDialog({required this.offer, required this.native});
  final UpdateOffer offer;
  final UpdateNative native;
  @override
  State<_UpdateDialog> createState() => _UpdateDialogState();
}

class _UpdateDialogState extends State<_UpdateDialog> {
  bool busy = false;
  String? status;

  Future<void> _update() async {
    setState(() {
      busy = true;
      status = 'Загрузка и проверка обновления…';
    });
    final outcome = await widget.native.install(widget.offer.signed);
    if (!mounted) return;
    setState(() {
      busy = false;
      status = updateOutcomeText(outcome);
    });
  }

  @override
  Widget build(BuildContext context) {
    final blocking = widget.offer.decision == UpdateDecision.blocking;
    final notes = widget.offer.manifest.releaseNotesRu.trim();
    return AlertDialog(
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
                  ? 'Эта версия DetourVPN больше не поддерживается. Установите версию ${widget.offer.manifest.versionName}, чтобы продолжить.'
                  : 'Доступна версия ${widget.offer.manifest.versionName}.',
            ),
            if (notes.isNotEmpty) ...[const SizedBox(height: 12), Text(notes)],
            if (status != null) ...[
              const SizedBox(height: 12),
              Text(status!, key: const ValueKey('update-status')),
            ],
          ],
        ),
      ),
      actions: [
        if (!blocking)
          TextButton(
            onPressed: busy ? null : () => Navigator.of(context).pop(),
            child: const Text('Позже'),
          ),
        FilledButton(
          onPressed: busy ? null : _update,
          child: Text(busy ? 'Загрузка…' : 'Скачать'),
        ),
      ],
    );
  }
}

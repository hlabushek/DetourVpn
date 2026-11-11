import 'dart:async';

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

abstract interface class UpdateNative {
  Future<int?> installedVersionCode();
  Future<String> install(UpdateManifest manifest);
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
  Future<String> install(UpdateManifest manifest) async {
    try {
      final value = await _channel.invokeMethod<Object>('appUpdateInstall', {
        'url': manifest.apkUrl,
        'sha256': manifest.apkSha256,
        'sizeBytes': manifest.apkSizeBytes,
        'versionCode': manifest.versionCode,
        'minSupportedVersionCode': manifest.minSupportedVersionCode,
      });
      return value is String ? value : 'download_failed';
    } catch (_) {
      return 'download_failed';
    }
  }
}

class UpdateOffer {
  const UpdateOffer(this.manifest, this.decision);
  final UpdateManifest manifest;
  final UpdateDecision decision;
}

/// One check per launch. Any failure (no manifest, network, invalid data) means no dialog.
Future<UpdateOffer?> checkForUpdate(TunnelApi api, UpdateNative native) async {
  try {
    final installed = await native.installedVersionCode();
    if (installed == null || installed < 1) return null;
    final manifest = UpdateManifest.parse(
      await api.call('GET', '/api/v1/app/update-manifest'),
    );
    if (manifest == null) return null;
    final decision = decideUpdate(installed, manifest);
    return decision == UpdateDecision.none
        ? null
        : UpdateOffer(manifest, decision);
  } catch (_) {
    return null;
  }
}

String updateOutcomeText(String outcome) => switch (outcome) {
  'installer_opened' => 'Подтвердите установку в системном окне Android.',
  'install_permission_required' =>
    'Разрешите DetourVPN устанавливать приложения в открывшихся настройках, затем вернитесь и нажмите «Обновить» ещё раз.',
  'hash_mismatch' || 'size_mismatch' || 'package_mismatch' =>
    'Загруженный файл не прошёл проверку и удалён. Попробуйте позже.',
  'not_newer' => 'У вас уже установлена актуальная версия.',
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
    final outcome = await widget.native.install(widget.offer.manifest);
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
                  : 'Вышла версия ${widget.offer.manifest.versionName}.',
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
          child: Text(busy ? 'Загрузка…' : 'Обновить'),
        ),
      ],
    );
  }
}

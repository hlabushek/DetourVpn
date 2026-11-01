import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'native_tunnel.dart';

const _reportableErrors = <String>{
  'admission_closed',
  'api_unavailable',
  'device_conflict',
  'device_limit_reached',
  'device_revoked',
  'insufficient_quota',
  'insufficient_window_funding',
  'invalid_response',
  'lease_expired',
  'login_failed',
  'native_disconnect_failed',
  'native_down',
  'native_unavailable',
  'network_unavailable',
  'permission_denied',
  'rate_limited',
  'remote_revoked',
  'revoke_unconfirmed',
  'session_device_mismatch',
  'session_erasure_failed',
  'session_expired',
  'session_storage_failed',
  'tunnel_node_capacity',
  'tunnel_node_unavailable',
  'tunnel_profile_unavailable',
  'tunnel_recovery_not_ready',
  'unauthorized',
  'window_generation_conflict',
  'window_node_required',
  'window_node_unavailable',
  'window_not_active',
  'window_tunnels_unavailable',
};

const _reportableStages = <String>{
  'android_vpn_permission',
  'connection_reconcile',
  'device_key_read',
  'device_registration',
  'key_challenge',
  'key_proof',
  'lease_create',
  'lease_poll',
  'lease_validation',
  'native_disconnect',
  'native_tunnel_start',
  'old_lease_cleanup',
  'selected_node',
};

@immutable
class DiagnosticReport {
  const DiagnosticReport({required this.errorCode, required this.stage});

  final String errorCode;
  final String stage;

  String get text =>
      'DetourVpn — отчёт диагностики\nКод: $errorCode\nЭтап: $stage';

  static DiagnosticReport fromError(String? error, String? stage) =>
      DiagnosticReport(
        errorCode: _reportableErrors.contains(error) ? error! : 'unknown_error',
        stage: _reportableStages.contains(stage) ? stage! : 'unknown',
      );
}

class DiagnosticReportButton extends StatelessWidget {
  const DiagnosticReportButton({
    super.key,
    required this.error,
    required this.stage,
  });

  final String error;
  final String? stage;

  Future<void> _share(BuildContext context) async {
    final report = DiagnosticReport.fromError(error, stage);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Предпросмотр отчёта'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'В отчёте только код ошибки и этап. Он не содержит данных '
              'аккаунта, ключей, адресов или истории трафика. Отправка '
              'начнётся только после выбора приложения Android.',
            ),
            const SizedBox(height: 12),
            SelectableText(report.text),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Выбрать приложение'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;

    try {
      await PlatformTunnel.channel.invokeMethod<void>('shareDiagnosticReport', {
        'code': report.errorCode,
        'stage': report.stage,
      });
    } on PlatformException {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Не удалось открыть отправку отчёта.')),
      );
    }
  }

  @override
  Widget build(BuildContext context) => TextButton.icon(
    key: const Key('diagnostic-share-button'),
    onPressed: () => _share(context),
    icon: const Icon(Icons.share_outlined),
    label: const Text('Поделиться диагностикой'),
  );
}

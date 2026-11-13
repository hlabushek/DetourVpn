import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'native_tunnel.dart';
import 'platform_info.dart';

const _reportableErrors = <String>{
  'admission_closed',
  'api_unavailable',
  'device_clock_skew',
  'device_conflict',
  'device_limit_reached',
  'device_revoked',
  'insufficient_quota',
  'insufficient_window_funding',
  'invalid_response',
  'lease_expired',
  'login_failed',
  'native_config_invalid',
  'native_disconnect_failed',
  'native_down',
  'native_engine_unavailable',
  'native_io_error',
  'native_permission_error',
  'native_restart_failed',
  'native_service_state_invalid',
  'native_start_failed',
  'native_unavailable',
  'network_unavailable',
  'permission_denied',
  'rate_limited',
  'remote_revoked',
  'renew_token_invalid',
  'revoke_unconfirmed',
  'session_device_mismatch',
  'session_erasure_failed',
  'session_expired',
  'session_storage_failed',
  'tunnel_config_not_ready',
  'tunnel_exists',
  'tunnel_idempotency_conflict',
  'tunnel_node_capacity',
  'tunnel_node_unavailable',
  'tunnel_profile_unavailable',
  'tunnel_recovery_not_ready',
  'unauthorized',
  'vpn_revoked',
  'window_activation_refused',
  'window_connection_exists',
  'window_deadline_not_advanced',
  'window_duration_invalid',
  'window_epoch_compare_failed',
  'window_epoch_conflict',
  'window_expired',
  'window_funding_conflict',
  'window_funding_deadline',
  'window_generation_conflict',
  'window_idempotency_conflict',
  'window_inconsistent',
  'window_legacy_tunnel_active',
  'window_manifest_protocol_conflict',
  'window_manifest_unavailable',
  'window_node_capacity',
  'window_node_required',
  'window_node_unavailable',
  'window_not_active',
  'window_not_found',
  'window_not_renewable',
  'window_operator_inventory_conflict',
  'window_operator_manifest_changed',
  'window_receipt_conflict',
  'window_receipt_not_found',
  'window_reconciliation_required',
  'window_replay_conflict',
  'window_runtime_mismatch',
  'window_tunnels_unavailable',
  'renewal_too_late',
  'refresh_reused',
  'connection_fenced',
  'runtime_unavailable',
  'device_traffic_cap_reached',
  'recovery_failed',
  'session_missing',
  'node_validation_failed',
  'invalid_origin',
  'background_start_refused',
  'not_found',
  'forbidden',
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

/// Task 7: did the app itself see a confirmed handshake before the failure.
const _reportableHandshakes = <String>{'confirmed', 'not_seen'};

@immutable
class DiagnosticReport {
  const DiagnosticReport({
    required this.errorCode,
    required this.stage,
    this.handshake,
  });

  final String errorCode;
  final String stage;

  /// null = not recorded (the line is omitted).
  final String? handshake;

  String get text =>
      'DetourVpn — отчёт диагностики\nКод: $errorCode\nЭтап: $stage'
      '${handshake == null ? '' : '\nHandshake: $handshake'}';

  static DiagnosticReport fromError(
    String? error,
    String? stage, {
    String? handshake,
  }) => DiagnosticReport(
    errorCode: _reportableErrors.contains(error) ? error! : 'unknown_error',
    stage: _reportableStages.contains(stage) ? stage! : 'unknown',
    handshake: _reportableHandshakes.contains(handshake) ? handshake : null,
  );
}

class DiagnosticReportButton extends StatelessWidget {
  const DiagnosticReportButton({
    super.key,
    required this.error,
    required this.stage,
    this.handshake,
  });

  final String error;
  final String? stage;
  final String? handshake;

  Future<void> _share(BuildContext context) async {
    final report = DiagnosticReport.fromError(
      error,
      stage,
      handshake: handshake,
    );
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Предпросмотр отчёта'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'В отчёте только код ошибки, этап, был ли handshake и коды состояния VPN '
              '(на Android). Он не содержит данных '
              'аккаунта, ключей, адресов или истории трафика. '
              '${isWindowsClient ? 'Отчёт будет скопирован в буфер обмена, ничего не отправляется автоматически.' : 'Отправка начнётся только после выбора приложения Android.'}',
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
            child: Text(isWindowsClient ? 'Скопировать' : 'Выбрать приложение'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;

    if (isWindowsClient) {
      // Windows has no share sheet (ADR-095): the fixed-category report goes to the clipboard only.
      await Clipboard.setData(ClipboardData(text: report.text));
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Отчёт скопирован. Отправьте его в чат поддержки.'),
        ),
      );
      return;
    }

    try {
      await PlatformTunnel.channel.invokeMethod<void>('shareDiagnosticReport', {
        'code': report.errorCode,
        'stage': report.stage,
        if (report.handshake != null) 'handshake': report.handshake,
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

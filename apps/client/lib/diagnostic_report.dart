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
  'recovery_exhausted',
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
  'panel_profile',
  'selected_node',
  // Stages added so that failures after the connect (polling, renewal, restart, resume) are named.
  'network_change',
  'session_resume',
  'tunnel_monitor',
  'tunnel_restart',
  'window_advance',
  'window_renewal',
};

/// Coarse context categories; values only, never a raw number, address or identifier.
const _reportableHandshakeAges = <String>{
  'never',
  'under_1m',
  'm1_to_5',
  'm5_to_30',
  'm30_to_120',
  'over_2h',
};
const _reportableWindows = <String>{'open', 'ended', 'unknown'};
const _reportableNetworkChanges = <String>{'none', 'recent', 'earlier'};

/// Rounds seconds since the last handshake into a fixed range (negative = no handshake).
String handshakeAgeBucket(int seconds) {
  if (seconds < 0) return 'never';
  if (seconds < 60) return 'under_1m';
  if (seconds < 300) return 'm1_to_5';
  if (seconds < 1800) return 'm5_to_30';
  if (seconds < 7200) return 'm30_to_120';
  return 'over_2h';
}

/// Network change age in ms (null = no change seen): recent is under a minute.
String networkChangeBucket(int? ageMs) => ageMs == null || ageMs < 0
    ? 'none'
    : ageMs < 60000
    ? 'recent'
    : 'earlier';

String windowBucket(int? remainingMs) =>
    remainingMs == null ? 'unknown' : (remainingMs > 0 ? 'open' : 'ended');

/// Russian explanation texts by hint key. Native keeps the same texts (sync test).
const diagnosticHints = <String, String>{
  'vpn_permission':
      'Вероятно, Android отозвал разрешение VPN (включили другой VPN или отключили). '
          'Что сделать: подключитесь заново и подтвердите запрос Android.',
  'login_needed':
      'Вероятно, вход в аккаунт устарел или устройство отозвано. '
          'Что сделать: войдите заново и подключитесь.',
  'network_lost':
      'Вероятно, не было сети или сервис недоступен. '
          'Что сделать: проверьте интернет, подождите минуту и подключитесь заново.',
  'server_busy':
      'Вероятно, сервер временно занят или недоступен. '
          'Что сделать: подождите пару минут и подключитесь заново.',
  'quota_out':
      'Вероятно, закончился доступный трафик или лимит. '
          'Что сделать: проверьте остаток на главном экране.',
  'renewal_failed':
      'Вероятно, окно не удалось продлить вовремя (телефон мог спать). '
          'Что сделать: подключитесь заново и напишите, как долго телефон был без работы.',
  'tunnel_dropped':
      'Вероятно, Android остановил туннель или перезапуск не удался. '
          'Что сделать: подключитесь заново; если повторяется, отключите экономию батареи для приложения.',
  'window_ended':
      'Вероятно, окно подключения закончилось. '
          'Что сделать: подключитесь заново.',
  'general':
      'Причина по этому коду не определена. '
          'Что сделать: отправьте этот отчёт и напишите, что делали перед сбоем.',
};

const _renewalCodes = <String>{
  'renewal_too_late',
  'window_not_renewable',
  'window_deadline_not_advanced',
  'window_epoch_conflict',
  'window_epoch_compare_failed',
  'window_generation_conflict',
};
const _permissionCodes = <String>{
  'vpn_revoked',
  'permission_denied',
  'native_permission_error',
};
const _loginCodes = <String>{
  'session_expired',
  'unauthorized',
  'login_failed',
  'refresh_reused',
  'renew_token_invalid',
  'session_missing',
  'device_revoked',
  'remote_revoked',
};
const _networkCodes = <String>{'network_unavailable', 'api_unavailable'};
const _busyCodes = <String>{
  'rate_limited',
  'window_node_capacity',
  'tunnel_node_capacity',
  'window_node_unavailable',
  'tunnel_node_unavailable',
  'admission_closed',
  'runtime_unavailable',
};
const _quotaCodes = <String>{
  'insufficient_quota',
  'insufficient_window_funding',
  'device_traffic_cap_reached',
};
const _tunnelCodes = <String>{
  'native_down',
  'native_restart_failed',
  'native_start_failed',
  'native_engine_unavailable',
  'native_unavailable',
  'native_io_error',
  'native_config_invalid',
  'background_start_refused',
};
const _endedCodes = <String>{
  'lease_expired',
  'window_expired',
  'window_not_active',
};

/// Picks the Russian hint key from the code and stage only. Never reads user data.
String diagnosticHintKey(String code, String stage) {
  if (_permissionCodes.contains(code)) return 'vpn_permission';
  if (_loginCodes.contains(code)) return 'login_needed';
  if (_renewalCodes.contains(code) ||
      stage == 'window_renewal' ||
      stage == 'window_advance') {
    return 'renewal_failed';
  }
  if (_networkCodes.contains(code)) return 'network_lost';
  if (_busyCodes.contains(code)) return 'server_busy';
  if (_quotaCodes.contains(code)) return 'quota_out';
  if (_tunnelCodes.contains(code) || stage == 'tunnel_restart') {
    return 'tunnel_dropped';
  }
  if (_endedCodes.contains(code)) return 'window_ended';
  return 'general';
}

/// Task 7: did the app itself see a confirmed handshake before the failure.
const _reportableHandshakes = <String>{'confirmed', 'not_seen'};

@immutable
class DiagnosticReport {
  const DiagnosticReport({
    required this.errorCode,
    required this.stage,
    this.handshake,
    this.handshakeAge,
    this.window,
    this.networkChange,
  });

  final String errorCode;
  final String stage;

  /// null = not recorded (the line is omitted).
  final String? handshake;

  /// Coarse context, each null = not recorded.
  final String? handshakeAge;
  final String? window;
  final String? networkChange;

  String get hintKey => diagnosticHintKey(errorCode, stage);

  String get text =>
      'DetourVpn — отчёт диагностики\nКод: $errorCode\nЭтап: $stage'
      '${handshake == null ? '' : '\nHandshake: $handshake'}'
      '${handshakeAge == null ? '' : '\nВремя с handshake: $handshakeAge'}'
      '${window == null ? '' : '\nОкно: $window'}'
      '${networkChange == null ? '' : '\nСмена сети: $networkChange'}'
      '\n${diagnosticHints[hintKey]}';

  static DiagnosticReport fromError(
    String? error,
    String? stage, {
    String? handshake,
    String? handshakeAge,
    String? window,
    String? networkChange,
  }) => DiagnosticReport(
    errorCode: _reportableErrors.contains(error) ? error! : 'unknown_error',
    stage: _reportableStages.contains(stage) ? stage! : 'unknown',
    handshake: _reportableHandshakes.contains(handshake) ? handshake : null,
    handshakeAge: _reportableHandshakeAges.contains(handshakeAge)
        ? handshakeAge
        : null,
    window: _reportableWindows.contains(window) ? window : null,
    networkChange: _reportableNetworkChanges.contains(networkChange)
        ? networkChange
        : null,
  );
}

class DiagnosticReportButton extends StatelessWidget {
  const DiagnosticReportButton({
    super.key,
    required this.error,
    required this.stage,
    this.handshake,
    this.handshakeAge,
    this.window,
    this.networkChange,
  });

  final String error;
  final String? stage;
  final String? handshake;
  final String? handshakeAge;
  final String? window;
  final String? networkChange;

  Future<void> _share(BuildContext context) async {
    final report = DiagnosticReport.fromError(
      error,
      stage,
      handshake: handshake,
      handshakeAge: handshakeAge,
      window: window,
      networkChange: networkChange,
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
              'В отчёте только код ошибки, этап, грубое время с последнего handshake, '
              'состояние окна и смены сети, подсказка о вероятной причине и коды '
              'состояния VPN (на Android). Он не содержит данных '
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
        if (report.handshakeAge != null) 'handshakeAge': report.handshakeAge,
        if (report.window != null) 'window': report.window,
        if (report.networkChange != null) 'networkChange': report.networkChange,
        'hint': report.hintKey,
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

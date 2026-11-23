import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_update.dart';
import 'connection.dart';
import 'dns_choice.dart';
import 'orbit_widgets.dart';
import 'platform_info.dart';
import 'split_apps.dart';
import 'ui/components.dart';
import 'ui/motion.dart';
import 'window_connection.dart';

/// The only place for device-local settings (opened from the gear).
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({
    super.key,
    required this.onTheme,
    required this.controller,
    this.onCheckUpdates,
    this.installedVersion,
  });
  final VoidCallback onTheme;
  final ConnectionController? controller;

  /// Installed version label (see `installedVersionLabel`); null while unknown.
  final String? installedVersion;

  /// ADR-107: manual update check; null hides the row (Windows, tests without an updater).
  /// The result is null when a check or an update dialog is already in progress.
  final Future<UpdateCheck?> Function()? onCheckUpdates;

  Future<void> _checkUpdates(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(
      const SnackBar(content: Text('Проверяем обновления…')),
    );
    final result = await onCheckUpdates!();
    messenger.hideCurrentSnackBar();
    if (result == null || result.status == UpdateCheckStatus.available) return;
    messenger.showSnackBar(
      SnackBar(content: Text(updateCheckText(result.status))),
    );
  }

  static const _vpnSettings = MethodChannel('project.vpn/tunnel.v1');

  /// ADR-187: split rules can be edited at any time. A change made while the VPN is up is
  /// stored now and applied at the next connect; the user may restart the tunnel right away.
  Future<void> _openSplitApps(
    BuildContext context,
    WindowConnectionController c,
  ) async {
    final navigator = Navigator.of(context);
    var changedWhileOn = false;
    await navigator.push(
      MaterialPageRoute<void>(
        builder: (_) => SplitAppsScreen(
          onSaved: () {
            if (c.hasLease || c.busy) changedWhileOn = true;
          },
        ),
      ),
    );
    if (!changedWhileOn || !c.hasLease || !context.mounted) return;
    final restart = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Применить новые правила?'),
        content: const Text(
          'Сохранено. Текущее подключение работает со старыми правилами. Перезапустить VPN сейчас или применить при следующем подключении?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Позже'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Перезапустить'),
          ),
        ],
      ),
    );
    if (restart == true) await c.restartTunnel();
  }

  /// Picks the tunnel DNS. The choice is stored natively and read at the next connect,
  /// so an active tunnel keeps its DNS until it is reconnected (ADR-106).
  Future<void> _openDnsChoice(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    String? current;
    try {
      current = parseDnsChoice(
        await _vpnSettings.invokeMethod<String>('dnsRead'),
      );
    } on PlatformException {
      messenger.showSnackBar(
        const SnackBar(content: Text('Не удалось прочитать настройку DNS')),
      );
      return;
    }
    if (!context.mounted) return;
    final picked = await showDialog<({String? value})>(
      context: context,
      builder: (dialogContext) => SimpleDialog(
        title: const Text('DNS для VPN'),
        children: [
          for (final option in <String?>[null, ...dnsChoiceServers])
            SimpleDialogOption(
              onPressed: () => Navigator.of(dialogContext).pop((value: option)),
              child: Row(
                children: [
                  Icon(
                    option == current
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off,
                  ),
                  const SizedBox(width: 12),
                  Expanded(child: Text(dnsChoiceLabel(option))),
                ],
              ),
            ),
        ],
      ),
    );
    if (picked == null || picked.value == current) return;
    try {
      final saved = await _vpnSettings.invokeMethod<String>('dnsWrite', {
        'server': dnsChoiceWire(picked.value),
      });
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            'DNS: ${dnsChoiceLabel(parseDnsChoice(saved))}. Применится при следующем подключении.',
          ),
        ),
      );
    } on PlatformException {
      messenger.showSnackBar(
        const SnackBar(
          content: Text('Не удалось сохранить DNS. Выключите VPN и повторите.'),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Настройки')),
    body: OrbitBackdrop(
      child: SafeArea(
        top: false,
        child: controller == null
            ? _content(context, null)
            : ListenableBuilder(
                listenable: controller!,
                builder: (context, _) => _content(context, controller),
              ),
      ),
    ),
  );

  Widget _content(BuildContext context, ConnectionController? c) {
    final text = Theme.of(context).textTheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: staggered([
        NavRow(
          icon: Icons.palette_outlined,
          title: 'Оформление',
          subtitle: dark ? 'Тёмная тема' : 'Светлая тема',
          onTap: onTheme,
        ),
        if (onCheckUpdates != null) ...[
          const SizedBox(height: 12),
          NavRow(
            icon: Icons.system_update_outlined,
            title: 'Проверить обновления',
            subtitle: installedVersion == null
                ? 'Новые версии также проверяются при запуске приложения'
                : 'Установлена версия $installedVersion',
            onTap: () => _checkUpdates(context),
          ),
        ],
        if (c is WindowConnectionController) ...[
          const SizedBox(height: 12),
          NavRow(
            icon: Icons.apps_rounded,
            title: 'Приложения вне VPN',
            subtitle: c.busy
                ? 'Подождите: идёт подключение или отключение'
                : c.hasLease
                ? 'VPN включён: изменения применятся при следующем подключении'
                : 'Исключённые приложения используют обычную сеть',
            enabled: !c.busy,
            onTap: () => _openSplitApps(context, c),
          ),
          // DNS for the tunnel (ADR-106): Android and Windows use the same channel methods.
          const SizedBox(height: 12),
          NavRow(
            icon: Icons.dns_outlined,
            title: 'DNS для VPN',
            subtitle: c.busy || c.hasLease
                ? 'Выберите после отключения VPN'
                : 'Применяется при следующем подключении',
            enabled: !c.busy && !c.hasLease,
            onTap: () => _openDnsChoice(context),
          ),
          // Android battery optimisation: not applicable on Windows (ADR-095).
          if (!isWindowsClient) ...[
            const SizedBox(height: 12),
            NavRow(
              icon: Icons.battery_saver_outlined,
              title: 'Работа в фоне',
              subtitle:
                  'Если VPN отключается при выключенном экране, разрешите DetourVPN работать без ограничений батареи',
              onTap: () async {
                try {
                  await _vpnSettings.invokeMethod<void>('openBatterySettings');
                } on PlatformException {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text(
                          'Откройте настройки Android → Приложения → DetourVPN → Батарея',
                        ),
                      ),
                    );
                  }
                }
              },
            ),
          ],
        ],
        // Android system VPN settings and Lockdown: no Always-on on Windows (ADR-095).
        if (!isWindowsClient) ...[
          const SizedBox(height: 12),
          NavRow(
            icon: Icons.settings_outlined,
            title: 'Системные настройки VPN',
            subtitle: 'Always-on и блокировка соединений в Android',
            onTap: () async {
              try {
                await _vpnSettings.invokeMethod<void>('openVpnSettings');
              } on PlatformException {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text('Не удалось открыть настройки VPN Android'),
                    ),
                  );
                }
              }
            },
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
            child: Text(
              'Восстановление проверяется только в debug-сборке и ещё не принято на устройстве. '
              'С Lockdown Android может блокировать исключённые приложения; при сбое VPN доступ останется заблокированным.',
              style: text.bodySmall,
            ),
          ),
        ],
        const SizedBox(height: 24),
        OrbitPanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('О бета-версии', style: text.titleMedium),
              const SizedBox(height: 8),
              Text(
                'Возможны сбои; работа в фоне и на разных сетях ещё проверяется. '
                'Статус VPN учитывает системный интерфейс и свежий handshake.',
                style: text.bodySmall,
              ),
              const SizedBox(height: 8),
              Text(
                c is WindowConnectionController
                    ? 'Продление требует связи с API и подтверждения узла. Учёт включает внешний UDP, заголовки и handshake. DNS идёт через VPN; IPv6 внутри VPN блокируется.'
                    : 'Внешний IP и DNS проверяются отдельно. Автоматического продления нет.',
                style: text.bodySmall,
              ),
              const SizedBox(height: 8),
              Text(
                'Просмотренные сайты и DNS-запросы не отправляются в API приложения.',
                style: text.bodySmall,
              ),
              if (isWindowsClient) ...[
                const SizedBox(height: 8),
                Text(
                  'Windows-версия: нужны права администратора. Аварийной блокировки нет — если VPN остановится, трафик пойдёт напрямую. Always-on не поддерживается.',
                  style: text.bodySmall,
                ),
              ],
            ],
          ),
        ),
      ]),
    );
  }
}

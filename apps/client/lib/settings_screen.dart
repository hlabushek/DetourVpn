import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'connection.dart';
import 'orbit_widgets.dart';
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
  });
  final VoidCallback onTheme;
  final ConnectionController? controller;
  static const _vpnSettings = MethodChannel('project.vpn/tunnel.v1');

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
        if (c is WindowConnectionController) ...[
          const SizedBox(height: 12),
          NavRow(
            icon: Icons.apps_rounded,
            title: 'Приложения вне VPN',
            subtitle: 'Исключённые приложения используют обычную сеть',
            enabled: !c.busy && !c.hasLease,
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => const SplitAppsScreen()),
            ),
          ),
        ],
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
            ],
          ),
        ),
      ]),
    );
  }
}

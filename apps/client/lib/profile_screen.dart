import 'package:flutter/material.dart';

import 'connection.dart';
import 'orbit_widgets.dart';
import 'ui/components.dart';
import 'ui/glass_nav.dart';
import 'ui/motion.dart';
import 'ui/tokens.dart';

/// Account only. Device settings live behind the gear (SettingsScreen); device
/// management and link codes live in the Telegram Mini App (ADR-093).
/// A guest never sees account actions; the server enforces this regardless.
class ProfileScreen extends StatelessWidget {
  const ProfileScreen({
    super.key,
    required this.onSignOut,
    required this.onDeleteAccount,
    required this.controller,
    this.guest = false,
    this.onLeaveDevice,
  });
  final bool guest;
  final Future<void> Function()? onLeaveDevice;
  final Future<void> Function() onSignOut;
  final Future<void> Function() onDeleteAccount;
  final ConnectionController controller;

  Future<void> _confirmDelete(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Удалить аккаунт?'),
        content: const Text(
          'VPN будет отключён, устройства и сессии отозваны, связь с вашим '
          'Telegram-аккаунтом удалена. Неиспользованный трафик пропадёт. '
          'Для возврата потребуется новое приглашение. Действие нельзя отменить.',
        ),
        actions: [
          TextButton(
            key: const Key('delete-account-cancel'),
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Отмена'),
          ),
          TextButton(
            key: const Key('delete-account-confirm'),
            style: TextButton.styleFrom(
              foregroundColor: OrbitTokens.of(dialogContext).danger,
            ),
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Удалить'),
          ),
        ],
      ),
    );
    if (confirmed == true) await onDeleteAccount();
  }

  Future<void> _confirmLeave(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Отключить это устройство?'),
        content: const Text(
          'VPN будет отключён, устройство удалено из аккаунта владельца. '
          'Чтобы подключиться снова, понадобится новый код.',
        ),
        actions: [
          TextButton(
            key: const Key('leave-device-cancel'),
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Отмена'),
          ),
          TextButton(
            key: const Key('leave-device-confirm'),
            style: TextButton.styleFrom(
              foregroundColor: OrbitTokens.of(dialogContext).danger,
            ),
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Отключить'),
          ),
        ],
      ),
    );
    if (confirmed == true) await onLeaveDevice?.call();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      final t = OrbitTokens.of(context);
      final text = Theme.of(context).textTheme;
      return ListView(
        padding: EdgeInsets.fromLTRB(16, 8, 16, 32 + GlassNavInset.of(context)),
        children: staggered([
          ScreenHeader(
            icon: Icons.person_outline_rounded,
            title: 'Профиль',
            subtitle: guest
                ? 'Информация об устройстве'
                : 'Управление аккаунтом',
          ),
          OrbitPanel(
            selected: true,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(
                      colors: [t.accent, Color.lerp(t.accent, t.cta, .5)!],
                    ),
                  ),
                  child: Icon(
                    guest ? Icons.smartphone_rounded : Icons.send_rounded,
                    color: t.onAccent,
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        guest ? 'Гостевое устройство' : 'Вход через Telegram',
                        style: text.titleMedium,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        guest
                            ? 'Подключено к аккаунту владельца по коду. Трафик списывается с его баланса; '
                                  'управлять аккаунтом и другими устройствами может только владелец.'
                            : 'Аккаунт привязан. Ключ устройства и сессия защищены Android Keystore.',
                        style: text.bodySmall,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          if (!guest) ...[
            const SizedBox(height: 12),
            const NavRow(
              icon: Icons.devices_other_rounded,
              title: 'Устройства и коды',
              subtitle:
                  'Подключайте и отключайте устройства в Telegram Mini App',
            ),
          ],
          const SizedBox(height: 32),
          if (guest)
            FilledButton.icon(
              key: const Key('leave-device-button'),
              onPressed: controller.busy ? null : () => _confirmLeave(context),
              icon: const Icon(Icons.link_off_rounded),
              label: const Text('Отключить это устройство'),
            )
          else ...[
            FilledButton.icon(
              onPressed: controller.busy ? null : onSignOut,
              icon: const Icon(Icons.logout_rounded),
              label: const Text('Выйти из аккаунта'),
            ),
            const SizedBox(height: 8),
            TextButton.icon(
              key: const Key('delete-account-button'),
              style: TextButton.styleFrom(foregroundColor: t.danger),
              onPressed: controller.busy ? null : () => _confirmDelete(context),
              icon: const Icon(Icons.delete_outline_rounded),
              label: const Text('Удалить аккаунт'),
            ),
          ],
          if (controller.error != null) ...[
            const SizedBox(height: 16),
            Text(
              messageFor(controller.error!),
              textAlign: TextAlign.center,
              style: TextStyle(color: t.danger),
            ),
          ],
        ]),
      );
    },
  );
}

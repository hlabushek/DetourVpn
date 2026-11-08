import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'connection.dart';
import 'country_flag.dart';
import 'diagnostic_report.dart';
import 'native_tunnel.dart';
import 'window_connection.dart';
import 'split_apps.dart';
import 'orbit_widgets.dart';
import 'quota.dart';
import 'quota_screen.dart';

class ConnectionScreen extends StatelessWidget {
  const ConnectionScreen({
    super.key,
    required this.controller,
    required this.onSignOut,
  });
  final ConnectionController controller;
  final Future<void> Function() onSignOut;

  void _nodes(BuildContext context) {
    unawaited(controller.refreshNodes());
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (context) => FractionallySizedBox(
        heightFactor: .8,
        child: ListenableBuilder(
          listenable: controller,
          builder: (context, _) {
            final c = controller;
            return ListView(
              padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
              children: [
                Text(
                  'Выберите сервер',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 12),
                const Text(
                  'Доступность по данным агента. Скорость и внешний IP здесь не измеряются.',
                ),
                if (c.hasLease)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 12),
                    child: Text(
                      'Сначала отключите VPN и дождитесь завершения подключения.',
                    ),
                  ),
                RadioGroup<String?>(
                  groupValue: c.selectedNode,
                  onChanged: (value) {
                    c.selectNode(value);
                    if (c is WindowConnectionController &&
                        c.selectedNode == value &&
                        c.native is NodePreferenceStore) {
                      unawaited(
                        _saveNodePreference(
                          context,
                          c.native as NodePreferenceStore,
                          value,
                        ),
                      );
                    }
                    Navigator.pop(context);
                  },
                  child: Column(
                    children: [
                      if (c is! WindowConnectionController)
                        RadioListTile<String?>(
                          value: null,
                          title: const Text('Первый доступный узел'),
                          enabled: !c.busy && !c.hasLease,
                        ),
                      for (final node in c.nodes)
                        RadioListTile<String?>(
                          value: node.id,
                          title: Row(
                            children: [
                              CountryFlag(node.countryCode),
                              const SizedBox(width: 10),
                              Flexible(
                                child: Text(
                                  '${node.name} · ${node.city ?? node.region}',
                                ),
                              ),
                            ],
                          ),
                          subtitle: Text(nodeMessage(node.reason)),
                          enabled:
                              !c.busy &&
                              !c.hasLease &&
                              c.nodesError == null &&
                              node.reason == 'available',
                        ),
                    ],
                  ),
                ),
                if (c.nodesLoading) const LinearProgressIndicator(),
                if (!c.nodesLoading && c.nodes.isEmpty && c.nodesError == null)
                  const Text(
                    'Серверы пока не настроены. Обновите список позже.',
                  ),
                if (c.nodesError != null)
                  const Text('Каталог не обновлён; прежние статусы устарели.'),
                if (c.nodesUpdatedAt != null)
                  Text(
                    'Проверка: ${c.nodesUpdatedAt!.toLocal().toString().substring(0, 19)}',
                  ),
                TextButton(
                  onPressed: c.nodesLoading ? null : c.refreshNodes,
                  child: const Text('Обновить список узлов'),
                ),
              ],
            );
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      final c = controller;
      final connected = c.phase == ConnectionPhase.connected;
      final waitingForServerClose =
          c is WindowConnectionController && c.remotePending;
      final failedAttemptWaitingForServerClose =
          waitingForServerClose && c.phase == ConnectionPhase.failed;
      final mustDisconnect =
          c.hasLease || c.busy || c.error == 'native_disconnect_failed';
      final title = switch (c.phase) {
        ConnectionPhase.disconnected => 'Отключено',
        ConnectionPhase.connecting => 'Подключение',
        ConnectionPhase.connected => 'Подключено',
        ConnectionPhase.reconnecting => 'Восстановление связи',
        ConnectionPhase.failed => 'Требуется действие',
      };
      final chosen = c.nodes.where((n) => n.id == c.selectedNode).firstOrNull;
      final q = c.quota.snapshot;
      return RefreshIndicator(
        onRefresh: () async {
          await c.refresh();
          await c.refreshQuota();
          await c.refreshNodes();
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          children: [
            Semantics(
              liveRegion: true,
              child: Column(
                children: [
                  Text(
                    title,
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    connected && c.error == null
                        ? 'VPN активен на этом устройстве'
                        : c.detail,
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            OrbitArtwork(
              child: Column(
                children: [
                  Icon(
                    connected
                        ? Icons.shield_outlined
                        : Icons.power_settings_new_rounded,
                    size: 32,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  const SizedBox(height: 16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      if (chosen != null) ...[
                        CountryFlag(chosen.countryCode),
                        const SizedBox(width: 10),
                      ],
                      Flexible(
                        child: Text(
                          chosen == null
                              ? (c is WindowConnectionController
                                    ? 'Выберите сервер'
                                    : 'Первый доступный узел')
                              : '${chosen.name} · ${chosen.city ?? chosen.region}',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: waitingForServerClose
                          ? null
                          : mustDisconnect
                          ? c.disconnect
                          : c.connect,
                      child: Text(
                        waitingForServerClose
                            ? failedAttemptWaitingForServerClose
                                  ? 'Ожидание закрытия попытки'
                                  : 'Ожидание подтверждения сервера'
                            : c.busy
                            ? 'Отменить подключение'
                            : mustDisconnect
                            ? 'Отключить VPN'
                            : 'Подключить',
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            OrbitPanel(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.public),
                title: const Text('Сменить регион'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _nodes(context),
              ),
            ),
            const SizedBox(height: 16),
            OrbitPanel(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Осталось трафика',
                          style: Theme.of(context).textTheme.labelLarge,
                        ),
                      ),
                      IconButton(
                        tooltip: 'Обновить остаток',
                        onPressed: c.quota.loading ? null : c.refreshQuota,
                        icon: const Icon(Icons.refresh, size: 20),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    q == null ? 'Нет данных' : compactBytes(q.available),
                    style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (q != null) ...[
                    const SizedBox(height: 10),
                    Semantics(
                      label: 'Точный остаток: ${byteText(q.available)}',
                      child: LinearProgressIndicator(
                        value: q.granted == 0 ? 0 : q.available / q.granted,
                        minHeight: 7,
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text('из ${compactBytes(q.granted)} начисленных'),
                  ],
                  if (c.quotaStale && q != null)
                    const Text('Данные устарели. Обновите остаток.'),
                  if (c.quota.error != null)
                    const Text(
                      'Не удалось обновить баланс. Показаны последние доступные данные.',
                    ),
                  Wrap(
                    spacing: 8,
                    children: [
                      TextButton(
                        onPressed: () {
                          unawaited(c.refreshQuota());
                          Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) => QuotaScreen(controller: c.quota),
                            ),
                          );
                        },
                        child: const Text('Трафик и история'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            if (c.error != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 16),
                child: Semantics(
                  liveRegion: true,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        messageFor(c.error!),
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                      if (diagnosticErrorDetails(
                            c.error,
                            c.diagnosticStage,
                            debugBuild: kDebugMode,
                          )
                          case final details?)
                        Text(details),
                      DiagnosticReportButton(
                        error: c.error!,
                        stage: c.diagnosticStage,
                      ),
                    ],
                  ),
                ),
              ),
            if (c.remotePending) ...[
              const SizedBox(height: 16),
              OrbitPanel(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Ожидается подтверждение удаления peer на сервере.',
                    ),
                    const SizedBox(height: 8),
                    Text(
                      c.phase == ConnectionPhase.failed
                          ? 'VPN на телефоне остановлен после ошибки. Сервер ещё подтверждает закрытие этой попытки; подключение станет доступно после подтверждения.'
                          : 'Дождитесь подтверждения закрытия соединения. При потере счётчиков резерв удерживается до операторской сверки.',
                    ),
                    if (c is! WindowConnectionController)
                      TextButton(
                        onPressed: c.canRecover ? c.recoverExpired : null,
                        child: const Text(
                          'Завершить истёкший lease со списанием резерва',
                        ),
                      ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 20),
            Text(
              c is WindowConnectionController
                  ? 'Продление требует связи с API и подтверждения узла. Учёт включает внешний UDP, заголовки и handshake. DNS идёт через VPN; IPv6 внутри VPN блокируется. Физическая проверка ещё требуется.'
                  : 'Внешний IP и DNS проверяются отдельно. Автоматического продления нет.',
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    },
  );

  Future<void> _saveNodePreference(
    BuildContext context,
    NodePreferenceStore store,
    String? selected,
  ) async {
    try {
      await store.writeSelectedNode(selected);
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Выбор узла не сохранён на этом устройстве'),
          ),
        );
      }
    }
  }
}

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({
    super.key,
    required this.onSignOut,
    required this.onDeleteAccount,
    required this.onTheme,
    required this.controller,
    this.guest = false,
    this.onLeaveDevice,
  });
  final bool guest;
  final Future<void> Function()? onLeaveDevice;
  final Future<void> Function() onSignOut;
  final Future<void> Function() onDeleteAccount;
  final VoidCallback onTheme;
  final ConnectionController controller;
  static const _vpnSettings = MethodChannel('project.vpn/tunnel.v1');

  Future<void> _confirmDelete(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Удалить аккаунт?'),
        content: const Text(
          'VPN будет отключён, устройства и сессии отозваны, связь с вашим '
          'Telegram-аккаунтом удалена. Неиспользованный тестовый трафик пропадёт. '
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
    builder: (context, _) => ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Text('Профиль', style: Theme.of(context).textTheme.headlineMedium),
        const SizedBox(height: 24),
        OrbitPanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.person_outline, size: 36),
              const SizedBox(height: 12),
              Text(guest ? 'Гостевое устройство' : 'Вход через Telegram'),
              const SizedBox(height: 8),
              Text(
                guest
                    ? 'Это устройство подключено к аккаунту владельца по коду. Трафик списывается с его баланса; '
                          'управлять аккаунтом и другими устройствами может только владелец.'
                    : 'Ключ устройства и сессия зашифрованы с помощью Android Keystore. После перезапуска VPN подключается вручную.',
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        OrbitPanel(
          child: ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.contrast),
            title: const Text('Оформление'),
            subtitle: Text(
              Theme.of(context).brightness == Brightness.dark
                  ? 'Тёмная тема'
                  : 'Светлая тема',
            ),
            trailing: const Icon(Icons.chevron_right),
            onTap: onTheme,
          ),
        ),
        const SizedBox(height: 16),
        OrbitPanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('О тестовой версии'),
              SizedBox(height: 12),
              Text(
                'Это бета-версия: возможны сбои, а работа в фоне и на разных сетях ещё проверяется. Статус VPN учитывает системный интерфейс и свежий handshake.',
              ),
              SizedBox(height: 12),
              Text(
                'Просмотренные сайты и DNS-запросы не отправляются в API приложения.',
              ),
            ],
          ),
        ),
        const SizedBox(height: 24),
        if (controller is WindowConnectionController)
          ListTile(
            title: const Text('Приложения вне VPN'),
            subtitle: const Text(
              'Исключённые приложения используют обычную сеть',
            ),
            enabled: !controller.busy && !controller.hasLease,
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => const SplitAppsScreen()),
            ),
          ),
        ListTile(
          leading: const Icon(Icons.security),
          title: const Text('Системные настройки VPN'),
          subtitle: const Text(
            'Always-on и блокировку соединений настраивает пользователь в Android. '
            'Восстановление проверяется только в debug-сборке и ещё не принято на устройстве. '
            'С Lockdown Android может блокировать исключённые приложения; при сбое VPN доступ останется заблокированным.',
          ),
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
        if (guest)
          OutlinedButton.icon(
            key: const Key('leave-device-button'),
            onPressed: controller.busy ? null : () => _confirmLeave(context),
            icon: const Icon(Icons.link_off),
            label: const Text('Отключить это устройство'),
          )
        else ...[
          OutlinedButton.icon(
            onPressed: controller.busy ? null : onSignOut,
            icon: const Icon(Icons.logout),
            label: const Text('Выйти из аккаунта'),
          ),
          TextButton.icon(
            key: const Key('delete-account-button'),
            onPressed: controller.busy ? null : () => _confirmDelete(context),
            icon: const Icon(Icons.delete_outline),
            label: const Text('Удалить аккаунт'),
          ),
        ],
        if (controller.error != null)
          Text(
            messageFor(controller.error!),
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
      ],
    ),
  );
}

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'app_update.dart';
import 'connection.dart';
import 'country_flag.dart';
import 'diagnostic_report.dart';
import 'orbit_widgets.dart';
import 'primary_action.dart';
import 'quota.dart';
import 'quota_screen.dart';
import 'region_picker.dart';
import 'ui/components.dart';
import 'ui/glass_nav.dart';
import 'ui/motion.dart';
import 'ui/tokens.dart';
import 'window_connection.dart';

/// VPN tab: status, orbit, the single connect/disconnect action, region and a
/// compact balance. Presentation only; all decisions stay in the controller.
class ConnectionScreen extends StatelessWidget {
  const ConnectionScreen({
    super.key,
    required this.controller,
    required this.onSignOut,
    this.onOpenTraffic,
    this.updateOffer,
    this.onOpenUpdate,
  });
  final ConnectionController controller;

  /// The newer build found by the last update check (ADR-107); null hides the banner. Null as a
  /// whole where there is no updater (Windows, tests).
  final ValueListenable<UpdateOffer?>? updateOffer;

  /// Opens the same update dialog and download flow as Settings.
  final void Function(UpdateOffer offer)? onOpenUpdate;
  final Future<void> Function() onSignOut;

  /// Switches to the Traffic tab. Without it (standalone use) the traffic
  /// screen is pushed instead.
  final VoidCallback? onOpenTraffic;

  void _openTraffic(BuildContext context) {
    unawaited(controller.refreshQuota());
    if (onOpenTraffic != null) {
      onOpenTraffic!();
      return;
    }
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => QuotaScreen(controller: controller.quota),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      final c = controller;
      final t = OrbitTokens.of(context);
      final text = Theme.of(context).textTheme;
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
      final orbit =
          c.busy ||
              c.phase == ConnectionPhase.connecting ||
              c.phase == ConnectionPhase.reconnecting
          ? OrbitState.connecting
          : c.phase == ConnectionPhase.failed || c.error != null
          ? OrbitState.error
          : connected
          ? OrbitState.connected
          : OrbitState.idle;
      final chosen = c.nodes.where((n) => n.id == c.selectedNode).firstOrNull;
      final q = c.quota.snapshot;
      final subtitle = connected && c.error == null
          ? 'VPN активен на этом устройстве'
          : c.detail;

      // One decision for both controls: the bottom button and the orbit share
      // `primary`, `runPrimary` and the same gate, so they cannot disagree or double-fire.
      final primary = resolvePrimaryAction(
        waitingForServerClose: waitingForServerClose,
        busy: c.busy,
        mustDisconnect: mustDisconnect,
      );
      void runPrimary() => PrimaryActionGate.shared.dispatch(
        primary,
        connect: c.connect,
        disconnect: c.disconnect,
        cancel: c.disconnect,
      );

      final Widget action;
      final String actionKey;
      switch (primary) {
        case PrimaryAction.none:
          actionKey = 'wait';
          action = FilledButton(
            onPressed: null,
            child: Text(
              failedAttemptWaitingForServerClose
                  ? 'Ожидание закрытия попытки'
                  : 'Ожидание подтверждения сервера',
              textAlign: TextAlign.center,
            ),
          );
        case PrimaryAction.cancel:
          actionKey = 'cancel';
          action = OutlinedButton(
            style: dangerOutlined(context),
            onPressed: runPrimary,
            child: const Text('Отменить подключение'),
          );
        case PrimaryAction.disconnect:
          actionKey = 'disconnect';
          action = FilledButton(
            onPressed: runPrimary,
            child: const Text('Отключить VPN'),
          );
        case PrimaryAction.connect:
          actionKey = 'connect';
          action = FilledButton(
            onPressed: runPrimary,
            child: Text(
              c.phase == ConnectionPhase.failed ? 'Повторить' : 'Подключить',
            ),
          );
      }

      return RefreshIndicator(
        onRefresh: () async {
          await c.refresh();
          await c.refreshQuota();
          await c.refreshNodes();
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: EdgeInsets.fromLTRB(
            16,
            8,
            16,
            32 + GlassNavInset.of(context),
          ),
          children: [
            if (updateOffer != null && onOpenUpdate != null)
              ValueListenableBuilder<UpdateOffer?>(
                valueListenable: updateOffer!,
                builder: (context, offer, _) => offer == null
                    ? const SizedBox.shrink()
                    : Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: UpdateBanner(
                          offer: offer,
                          onOpen: () => onOpenUpdate!(offer),
                        ),
                      ),
              ),
            Semantics(
              liveRegion: true,
              child: OrbitSwitcher(
                child: Column(
                  key: ValueKey('$title|$subtitle'),
                  children: [
                    Text(
                      title,
                      textAlign: TextAlign.center,
                      style: text.headlineMedium,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      subtitle,
                      textAlign: TextAlign.center,
                      style: text.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            // Second tap target for the same action as the bottom button. No ripple,
            // no label: the artwork looks the same. Disabled when the button is disabled.
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: primary == PrimaryAction.none ? null : runPrimary,
              child: OrbitBlob(state: orbit, height: 290),
            ),
            const SizedBox(height: 16),
            OrbitSwitcher(
              duration: OrbitMotion.fast,
              offset: 0,
              alignment: Alignment.center,
              child: SizedBox(
                key: ValueKey(actionKey),
                width: double.infinity,
                child: action,
              ),
            ),
            const SizedBox(height: 16),
            OrbitAppear(
              index: 1,
              child: NavRow(
                icon: Icons.public,
                leading: chosen == null
                    ? null
                    : CountryFlag(chosen.countryCode, width: 40),
                title: chosen == null
                    ? (c is WindowConnectionController
                          ? 'Выберите сервер'
                          : 'Первый доступный узел')
                    : nodeTitles(c.nodes)[chosen.id]!,
                subtitle: 'Сменить регион',
                onTap: () => RegionPickerScreen.open(context, c),
                trailing: chosen?.probeHost == null
                    ? null
                    : Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          LatencyLabel(
                            ms: c.latencyMs[chosen!.id],
                            measuring:
                                c.latencyMeasuring &&
                                !c.latencyMs.containsKey(chosen.id),
                          ),
                          IconButton(
                            tooltip: 'Обновить пинг',
                            onPressed: c.latencyMeasuring
                                ? null
                                : c.measureLatency,
                            icon: const Icon(Icons.refresh_rounded),
                          ),
                        ],
                      ),
              ),
            ),
            const SizedBox(height: 12),
            OrbitAppear(
              index: 2,
              child: OrbitPanel(
                onTap: () => _openTraffic(context),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'Осталось трафика',
                            style: text.bodySmall,
                          ),
                        ),
                        Icon(Icons.chevron_right_rounded, color: t.textMuted),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Wrap(
                      crossAxisAlignment: WrapCrossAlignment.end,
                      spacing: 8,
                      children: [
                        Text(
                          q == null ? 'Нет данных' : compactBytes(q.available),
                          style: text.headlineMedium,
                        ),
                        if (q != null)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 4),
                            child: Text(
                              'из ${compactBytes(q.granted)}',
                              style: text.bodySmall,
                            ),
                          ),
                      ],
                    ),
                    if (q != null) ...[
                      const SizedBox(height: 12),
                      Semantics(
                        label: 'Точный остаток: ${byteText(q.available)}',
                        child: UsageBar(
                          value: q.granted == 0 ? 0 : q.available / q.granted,
                        ),
                      ),
                    ],
                    if (c.quotaStale && q != null) ...[
                      const SizedBox(height: 8),
                      Text(
                        'Данные устарели. Обновите остаток.',
                        style: text.bodySmall?.copyWith(color: t.warning),
                      ),
                    ],
                    if (c.quota.error != null) ...[
                      const SizedBox(height: 8),
                      Text(
                        'Не удалось обновить баланс. Показаны последние доступные данные.',
                        style: text.bodySmall?.copyWith(color: t.warning),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            // ADR-139: Always-on VPN status, only when native reported it off or in Lockdown.
            if (alwaysOnHint(c.alwaysOnStatus) case final hint?) ...[
              const SizedBox(height: 16),
              OrbitNotice(text: hint),
            ],
            if (c.error != null) ...[
              const SizedBox(height: 16),
              OrbitNotice(
                danger: true,
                text: messageFor(c.error!),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (diagnosticErrorDetails(
                          c.error,
                          c.diagnosticStage,
                          debugBuild: kDebugMode,
                        )
                        case final details?)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text(details, style: text.bodySmall),
                      ),
                    DiagnosticReportButton(
                      error: c.error!,
                      stage: c.diagnosticStage,
                      handshake: c.diagnosticHandshake,
                      handshakeAge: c.diagnosticHandshakeAge,
                      window: c.diagnosticWindow,
                      networkChange: c.diagnosticNetworkChange,
                    ),
                  ],
                ),
              ),
            ],
            if (c.remotePending) ...[
              const SizedBox(height: 16),
              OrbitNotice(
                text: 'Ожидается подтверждение удаления peer на сервере.',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SizedBox(height: 8),
                    Text(
                      c.phase == ConnectionPhase.failed
                          ? 'VPN на телефоне остановлен после ошибки. Сервер ещё подтверждает закрытие этой попытки; подключение станет доступно после подтверждения.'
                          : 'Дождитесь подтверждения закрытия соединения. При потере счётчиков резерв удерживается до операторской сверки.',
                      style: text.bodySmall,
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
          ],
        ),
      );
    },
  );
}

/// Compact home-screen notice that a newer build is published; the button opens the update dialog.
class UpdateBanner extends StatelessWidget {
  const UpdateBanner({super.key, required this.offer, required this.onOpen});
  final UpdateOffer offer;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    return Semantics(
      liveRegion: true,
      child: OrbitPanel(
        key: const ValueKey('update-banner'),
        tone: t.accent,
        padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
        child: Row(
          children: [
            Icon(Icons.system_update_outlined, color: t.accent, size: 22),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                updateBannerText(offer),
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ),
            TextButton(onPressed: onOpen, child: const Text('Обновить')),
          ],
        ),
      ),
    );
  }
}

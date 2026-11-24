import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'connection.dart';
import 'connection_status.dart';
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
  });
  final ConnectionController controller;

  // ADR-193: the update banner moved out of this screen to the app-wide slot under the app
  // bar (update_ui.dart), so it is visible on every tab.
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
      // ADR-192: one decision for the status, the orbit and both controls (the bottom
      // button and the orbit share `primary`, `runPrimary` and the same gate), so the
      // picture, the text and the action cannot disagree or double-fire.
      final view = resolveHomeView(
        phase: c.phase,
        busy: c.busy,
        disconnecting: c.disconnecting,
        hasLease: c.hasLease,
        remotePending: c.remotePending,
        canRetryClose: c is WindowConnectionController,
        error: c.error,
      );
      final title = view.title;
      final orbit = view.orbit(hasError: c.error != null);
      final chosen = c.nodes.where((n) => n.id == c.selectedNode).firstOrNull;
      final q = c.quota.snapshot;
      // ADR-162: a lease on offline grace is shown as degraded, with the time left.
      final degraded = c is WindowConnectionController && c.offlineGrace;
      final subtitle = switch (view.state) {
        HomeState.connected when c.error == null && !degraded =>
          'VPN активен на этом устройстве',
        HomeState.disconnecting =>
          'VPN выключается, подключение закрывается на сервере',
        // ADR-154: a tap that waits for a running check keeps the old phase and text.
        HomeState.connecting when c.phase != ConnectionPhase.connecting =>
          'Проверяется текущее состояние VPN',
        HomeState.closing => 'VPN на телефоне выключен',
        // ADR-192 decision B: the subtitle names the step the button offers.
        HomeState.failed || HomeState.disconnected
            when view.action == PrimaryAction.signIn =>
          'VPN не подключён. Нужно войти снова',
        HomeState.failed || HomeState.disconnected
            when view.action == PrimaryAction.openTraffic =>
          'VPN не подключён: недостаточно трафика',
        // The controller's text may still describe the step that failed; the cause is below.
        HomeState.failed when c.error != null =>
          'VPN не подключён. Причина указана ниже',
        HomeState.stopNeeded =>
          'VPN мог остаться включённым. Нажмите «Отключить VPN»',
        _ => c.detail,
      };
      final primary = view.action;
      void runPrimary() => PrimaryActionGate.shared.dispatch(
        primary,
        connect: c.connect,
        disconnect: c.disconnect,
        cancel: c.disconnect,
        retry: c is WindowConnectionController ? c.retryRevoke : null,
        // Decision B: the existing sign-out path (to the login screen) and Traffic tab.
        signIn: onSignOut,
        openTraffic: () async => _openTraffic(context),
      );

      final Widget action;
      final String actionKey;
      final label = Text(view.label, textAlign: TextAlign.center);
      switch (primary) {
        case PrimaryAction.none:
          actionKey = 'wait';
          action = FilledButton(onPressed: null, child: label);
        case PrimaryAction.retryRevoke:
          // ADR-145 (2.5): retry the server close now instead of a disabled button.
          actionKey = 'retry-revoke';
          action = FilledButton(onPressed: runPrimary, child: label);
        case PrimaryAction.cancel:
          actionKey = 'cancel';
          action = OutlinedButton(
            style: dangerOutlined(context),
            onPressed: runPrimary,
            child: label,
          );
        case PrimaryAction.disconnect:
          actionKey = 'disconnect';
          action = FilledButton(onPressed: runPrimary, child: label);
        case PrimaryAction.connect:
          actionKey = 'connect';
          action = FilledButton(onPressed: runPrimary, child: label);
        case PrimaryAction.signIn:
          actionKey = 'sign-in';
          action = FilledButton(onPressed: runPrimary, child: label);
        case PrimaryAction.openTraffic:
          actionKey = 'open-traffic';
          action = FilledButton(onPressed: runPrimary, child: label);
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
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: ConnectionLiveStatus(controller: c),
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
                text: 'Сервер ещё не подтвердил закрытие прошлого подключения.',
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
                    // ADR-145 (2.5): give up waiting here; the next connect revokes it again.
                    if (c is WindowConnectionController) ...[
                      const SizedBox(height: 8),
                      Text(
                        '«Сбросить» выключает VPN и снимает ожидание в приложении. При следующем подключении приложение снова попросит сервер закрыть старое.',
                        style: text.bodySmall,
                      ),
                      TextButton(
                        key: const ValueKey('force-reset'),
                        onPressed: c.busy ? null : c.forceReset,
                        child: const Text('Сбросить'),
                      ),
                    ],
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

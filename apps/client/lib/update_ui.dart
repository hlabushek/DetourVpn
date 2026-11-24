import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'app_update.dart';
import 'orbit_widgets.dart';
import 'ui/motion.dart';
import 'ui/tokens.dart';

/// ADR-193: the in-app update banner. It sits under the app bar on every screen and slides in
/// when a check finds a newer build, and out when it is hidden or no longer offered. All logic
/// stays in [UpdateScheduler] and [UpdateDownload]; this is presentation only.
class UpdateBannerSlot extends StatelessWidget {
  const UpdateBannerSlot({
    super.key,
    required this.offer,
    required this.download,
    required this.onOpen,
    required this.onDismiss,
  });
  final ValueListenable<UpdateOffer?> offer;
  final ValueListenable<UpdateDownloadState> download;
  final void Function(UpdateOffer offer) onOpen;

  /// Hides the banner of an optional build for this session. Never offered for a blocking one.
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<UpdateOffer?>(
    valueListenable: offer,
    builder: (context, value, _) => AnimatedSwitcher(
      duration: OrbitMotion.of(context, OrbitMotion.medium),
      switchInCurve: OrbitMotion.emphasized,
      switchOutCurve: Curves.easeIn,
      layoutBuilder: (current, previous) => Stack(
        alignment: Alignment.topCenter,
        children: [...previous, ?current],
      ),
      transitionBuilder: (child, animation) => SizeTransition(
        sizeFactor: animation,
        alignment: Alignment.topCenter,
        child: SlideTransition(
          position: Tween(
            begin: const Offset(0, -0.6),
            end: Offset.zero,
          ).animate(animation),
          child: FadeTransition(opacity: animation, child: child),
        ),
      ),
      child: value == null
          ? const SizedBox(key: ValueKey('update-banner-none'), width: 0)
          : Padding(
              key: ValueKey(
                'update-banner-${value.manifest.versionCode}-${value.decision.name}',
              ),
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: ValueListenableBuilder<UpdateDownloadState>(
                valueListenable: download,
                builder: (context, state, _) => UpdateBanner(
                  offer: value,
                  state: state,
                  onOpen: () => onOpen(value),
                  onDismiss: value.decision == UpdateDecision.blocking
                      ? null
                      : onDismiss,
                ),
              ),
            ),
    ),
  );
}

/// One-line status under the banner title.
String updateBannerDetail(
  UpdateOffer offer,
  UpdateDownloadState state,
) => switch (state.stage) {
  UpdateStage.downloading when state.cancelling => 'Отмена загрузки…',
  UpdateStage.downloading => updateProgressText(state.received, state.total),
  UpdateStage.verifying => 'Проверка файла…',
  UpdateStage.installing => 'Открываем установщик Android…',
  UpdateStage.ready => 'Файл скачан и проверен. Готово к установке',
  UpdateStage.cancelled => 'Загрузка отменена',
  UpdateStage.failed => 'Загрузка не удалась',
  UpdateStage.idle =>
    'Размер ${formatUpdateMegabytes(offer.manifest.apkSizeBytes)}. Нажмите, чтобы узнать, что нового',
};

class UpdateBanner extends StatelessWidget {
  const UpdateBanner({
    super.key,
    required this.offer,
    required this.state,
    required this.onOpen,
    this.onDismiss,
  });
  final UpdateOffer offer;
  final UpdateDownloadState state;
  final VoidCallback onOpen;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final text = Theme.of(context).textTheme;
    final blocking = offer.decision == UpdateDecision.blocking;
    final tone = blocking ? t.warning : t.accent;
    return Semantics(
      liveRegion: true,
      container: true,
      child: OrbitPanel(
        key: const ValueKey('update-banner'),
        tone: tone,
        onTap: onOpen,
        padding: const EdgeInsets.fromLTRB(14, 10, 4, 10),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(
                  state.stage == UpdateStage.ready
                      ? Icons.download_done_rounded
                      : Icons.system_update_outlined,
                  color: tone,
                  size: 22,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(updateBannerText(offer), style: text.titleSmall),
                      const SizedBox(height: 2),
                      Text(
                        updateBannerDetail(offer, state),
                        key: const ValueKey('update-banner-detail'),
                        style: text.bodySmall,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
                TextButton(
                  key: const ValueKey('update-banner-open'),
                  onPressed: onOpen,
                  child: Text(
                    state.stage == UpdateStage.idle ? 'Обновить' : 'Открыть',
                  ),
                ),
                if (onDismiss != null)
                  IconButton(
                    key: const ValueKey('update-banner-dismiss'),
                    tooltip: 'Скрыть до следующего запуска',
                    visualDensity: VisualDensity.compact,
                    onPressed: onDismiss,
                    icon: const Icon(Icons.close_rounded, size: 20),
                  ),
              ],
            ),
            if (state.stage == UpdateStage.downloading)
              Padding(
                padding: const EdgeInsets.fromLTRB(34, 8, 10, 0),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(2),
                  child: LinearProgressIndicator(
                    key: const ValueKey('update-banner-progress'),
                    value: state.fraction ?? 0,
                    minHeight: 3,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// ADR-193: the update sheet. A blocking build cannot be dismissed (no drag, no tap outside, no
/// back, no «Позже»). [download] is the app-wide download, so reopening shows the same progress.
Future<void> showUpdateSheet(
  BuildContext context,
  UpdateOffer offer,
  UpdateDownload download,
) {
  final blocking = offer.decision == UpdateDecision.blocking;
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    isDismissible: !blocking,
    enableDrag: !blocking,
    showDragHandle: !blocking,
    builder: (_) => PopScope(
      canPop: !blocking,
      child: UpdateSheet(offer: offer, download: download),
    ),
  );
}

class UpdateSheet extends StatelessWidget {
  const UpdateSheet({super.key, required this.offer, required this.download});
  final UpdateOffer offer;
  final UpdateDownload download;

  bool get _blocking => offer.decision == UpdateDecision.blocking;

  void _download() => unawaited(
    download.start(offer.signed, expectedTotal: offer.manifest.apkSizeBytes),
  );

  void _install() => unawaited(download.install(offer.signed));

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final text = Theme.of(context).textTheme;
    final notes = offer.manifest.releaseNotesRu.trim();
    final duration = OrbitMotion.of(context, OrbitMotion.medium);
    return ValueListenableBuilder<UpdateDownloadState>(
      valueListenable: download.state,
      builder: (context, state, _) => SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(24, _blocking ? 24 : 0, 24, 24),
        child: AnimatedSize(
          duration: duration,
          curve: OrbitMotion.emphasized,
          alignment: Alignment.topCenter,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  CircleAvatar(
                    radius: 22,
                    backgroundColor: t.accent.withValues(alpha: 0.15),
                    child: Icon(Icons.system_update_rounded, color: t.accent),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _blocking
                              ? 'Нужно обновить приложение'
                              : 'Доступно обновление',
                          style: text.titleLarge,
                        ),
                        const SizedBox(height: 2),
                        Text(
                          'Версия ${offer.manifest.versionName} · ${formatUpdateMegabytes(offer.manifest.apkSizeBytes)}',
                          style: text.bodySmall,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (_blocking) ...[
                const SizedBox(height: 16),
                Text(
                  'Эта версия DetourVPN больше не поддерживается. Установите версию ${offer.manifest.versionName}, чтобы продолжить.',
                ),
              ],
              if (notes.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text('Что нового', style: text.labelLarge),
                const SizedBox(height: 6),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 180),
                  child: SingleChildScrollView(child: Text(notes)),
                ),
              ],
              const SizedBox(height: 20),
              AnimatedSwitcher(
                duration: duration,
                switchInCurve: OrbitMotion.emphasized,
                transitionBuilder: (child, animation) => FadeTransition(
                  opacity: animation,
                  child: SizeTransition(
                    sizeFactor: animation,
                    alignment: Alignment.topCenter,
                    child: child,
                  ),
                ),
                child: KeyedSubtree(
                  key: ValueKey('update-stage-${_panelKey(state)}'),
                  child: _status(context, state),
                ),
              ),
              const SizedBox(height: 16),
              AnimatedSwitcher(
                duration: OrbitMotion.of(context, OrbitMotion.fast),
                child: KeyedSubtree(
                  key: ValueKey('update-actions-${state.stage.name}'),
                  child: _actions(context, state),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Progress stages share one panel so the bar moves smoothly instead of being rebuilt.
  static String _panelKey(UpdateDownloadState state) => switch (state.stage) {
    UpdateStage.downloading ||
    UpdateStage.verifying ||
    UpdateStage.installing => 'progress',
    final other => other.name,
  };

  Widget _status(BuildContext context, UpdateDownloadState state) {
    final t = OrbitTokens.of(context);
    final muted = Theme.of(context).textTheme.bodySmall;
    Widget line(IconData icon, Color color, String message) => Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, color: color, size: 22),
        const SizedBox(width: 10),
        Expanded(child: Text(message, key: const ValueKey('update-status'))),
      ],
    );
    switch (state.stage) {
      case UpdateStage.idle:
        return Text(
          'Файл скачается внутри приложения и будет проверен перед установкой.',
          key: const ValueKey('update-status'),
          style: muted,
        );
      case UpdateStage.downloading:
      case UpdateStage.verifying:
      case UpdateStage.installing:
        final determinate = state.stage == UpdateStage.downloading;
        final fraction = state.fraction;
        final label = switch (state.stage) {
          _ when state.cancelling => 'Отмена загрузки…',
          UpdateStage.verifying => 'Проверка файла…',
          UpdateStage.installing => 'Открываем установщик Android…',
          _ => updateProgressText(state.received, state.total),
        };
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: TweenAnimationBuilder<double>(
                tween: Tween(end: fraction ?? 0),
                duration: OrbitMotion.of(context, OrbitMotion.fast),
                builder: (context, value, _) => LinearProgressIndicator(
                  key: const ValueKey('update-progress'),
                  value: determinate ? value : null,
                  minHeight: 8,
                  semanticsLabel: 'Загрузка обновления',
                  semanticsValue: determinate && fraction != null
                      ? '${(fraction * 100).floor()} %'
                      : null,
                ),
              ),
            ),
            const SizedBox(height: 10),
            Text(label, key: const ValueKey('update-status')),
            if (!_blocking &&
                !state.cancelling &&
                state.stage == UpdateStage.downloading) ...[
              const SizedBox(height: 4),
              Text(
                'Окно можно скрыть: загрузка продолжится, а кнопка «Установить» появится здесь.',
                style: muted,
              ),
            ],
          ],
        );
      case UpdateStage.ready:
        return line(
          Icons.check_circle_rounded,
          t.success,
          updateOutcomeText(state.outcome ?? 'verified'),
        );
      case UpdateStage.cancelled:
        return line(
          Icons.cancel_outlined,
          t.textMuted,
          updateOutcomeText('cancelled'),
        );
      case UpdateStage.failed:
        return line(
          Icons.error_outline_rounded,
          t.danger,
          updateOutcomeText(state.outcome ?? 'download_failed'),
        );
    }
  }

  Widget _actions(BuildContext context, UpdateDownloadState state) {
    void close() => Navigator.of(context).pop();
    final later = _blocking
        ? null
        : TextButton(onPressed: close, child: const Text('Позже'));
    final hide = _blocking
        ? null
        : TextButton(onPressed: close, child: const Text('Скрыть'));
    final closeButton = _blocking
        ? null
        : TextButton(onPressed: close, child: const Text('Закрыть'));
    FilledButton primary(String label, VoidCallback? onPressed) => FilledButton(
      key: const ValueKey('update-primary'),
      onPressed: onPressed,
      child: Text(label),
    );
    final List<Widget> buttons = switch (state.stage) {
      UpdateStage.idle => [?later, primary('Скачать', _download)],
      UpdateStage.downloading || UpdateStage.verifying => [
        ?hide,
        OutlinedButton(
          key: const ValueKey('update-cancel'),
          onPressed: state.cancelling || state.stage != UpdateStage.downloading
              ? null
              : () => unawaited(download.cancel()),
          child: const Text('Отменить загрузку'),
        ),
      ],
      UpdateStage.installing => [?hide, primary('Установить', null)],
      UpdateStage.ready => [?later, primary('Установить', _install)],
      UpdateStage.cancelled => [
        ?closeButton,
        primary('Скачать снова', _download),
      ],
      UpdateStage.failed => [
        ?closeButton,
        // Blocking: always a way forward. Otherwise only where a new download can help.
        if (state.retryable || _blocking)
          primary('Повторить загрузку', _download),
      ],
    };
    return Wrap(
      alignment: WrapAlignment.end,
      spacing: 8,
      runSpacing: 8,
      children: buttons,
    );
  }
}

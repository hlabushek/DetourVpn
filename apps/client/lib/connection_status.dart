import 'dart:async';

import 'package:flutter/material.dart';

import 'connected_since.dart';
import 'connection.dart';

/// Short Russian duration: «12 с», «3 мин 5 с», «1 ч 2 мин». Negative values count as zero.
String shortDurationText(int seconds) {
  final s = seconds < 0 ? 0 : seconds;
  if (s < 60) return '$s с';
  if (s < 3600) {
    final rest = s % 60;
    return rest == 0 ? '${s ~/ 60} мин' : '${s ~/ 60} мин $rest с';
  }
  final minutes = (s % 3600) ~/ 60;
  return minutes == 0 ? '${s ~/ 3600} ч' : '${s ~/ 3600} ч $minutes мин';
}

/// «Последний handshake 12 с назад»; «Handshake ещё не получен» for a negative age (none since
/// the tunnel started); null when unknown. [elapsedMs] is the time since the value was read.
String? handshakeStatusText(int? ageSec, {int elapsedMs = 0}) {
  if (ageSec == null) return null;
  if (ageSec < 0) return 'Handshake ещё не получен';
  return 'Последний handshake ${shortDurationText(ageSec + elapsedMs ~/ 1000)} назад';
}

/// «Следующее продление через 40 с» until the current window's renewal starts (renewal begins
/// [renewLeadMs] before the window ends), then «Идёт продление окна». Null when the window time
/// is unknown or over, or windows are not renewed.
String? renewalStatusText(
  int? remainingMs,
  int? renewLeadMs, {
  int elapsedMs = 0,
}) {
  if (remainingMs == null || renewLeadMs == null) return null;
  final remaining = remainingMs - elapsedMs;
  if (remaining <= 0) return null;
  final untilRenewal = remaining - renewLeadMs;
  if (untilRenewal <= 0) return 'Идёт продление окна';
  // Round up, so the line never says «через 0 с» while renewal has not started.
  return 'Следующее продление через ${shortDurationText((untilRenewal + 999) ~/ 1000)}';
}

/// Whether the home screen shows live connection details for [phase].
bool showsLiveStatus(ConnectionPhase phase) =>
    phase == ConnectionPhase.connected || phase == ConnectionPhase.reconnecting;

/// Live lines under the connection title: connection timer, last handshake and next renewal. Re-renders every second
/// only while visible (the app in the foreground and this widget mounted).
class ConnectionLiveStatus extends StatefulWidget {
  const ConnectionLiveStatus({super.key, required this.controller});
  final ConnectionController controller;

  @override
  State<ConnectionLiveStatus> createState() => _ConnectionLiveStatusState();
}

class _ConnectionLiveStatusState extends State<ConnectionLiveStatus>
    with WidgetsBindingObserver {
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final state = WidgetsBinding.instance.lifecycleState;
    if (state == null || state == AppLifecycleState.resumed) _start();
  }

  void _start() => _tick ??= Timer.periodic(
    const Duration(seconds: 1),
    (_) => setState(() {}),
  );

  void _stop() {
    _tick?.cancel();
    _tick = null;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _start();
      setState(() {});
    } else {
      _stop();
    }
  }

  @override
  void dispose() {
    _stop();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    if (!showsLiveStatus(c.phase)) return const SizedBox.shrink();
    final now = DateTime.now();
    final sampled = c.statusSampledAt;
    final elapsed = sampled == null
        ? 0
        : now.difference(sampled).inMilliseconds.clamp(0, 1 << 31);
    // ADR-144: «Подключено …» only under the «Подключено» title, never under «Восстановление связи».
    final timer = c.phase == ConnectionPhase.connected
        ? connectedTimerText(c.connectedSinceMs, now.millisecondsSinceEpoch)
        : null;
    final lines = [
      ?handshakeStatusText(c.statusHandshakeAgeSec, elapsedMs: elapsed),
      ?renewalStatusText(
        c.statusRemainingMs,
        c.renewLeadMs,
        elapsedMs: elapsed,
      ),
    ];
    final text = Theme.of(context).textTheme;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (timer != null)
          Text(
            timer,
            key: const ValueKey('connection-timer'),
            textAlign: TextAlign.center,
            style: text.titleMedium?.copyWith(
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        if (lines.isNotEmpty)
          Text(
            lines.join('\n'),
            key: const ValueKey('connection-live-status'),
            textAlign: TextAlign.center,
            style: text.bodySmall,
          ),
      ],
    );
  }
}

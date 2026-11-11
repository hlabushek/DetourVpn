import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../orbit_widgets.dart';
import 'motion.dart';
import '../latency_probe.dart';
import 'tokens.dart';

/// User-facing server state, derived only from the catalog `reason` (ADR-092).
/// Latency is shown separately and only when measured (ADR-102); speed is never shown.
enum ServerState { available, busy, unavailable }

ServerState serverState(String reason) => switch (reason) {
  'available' => ServerState.available,
  'capacity' => ServerState.busy,
  _ => ServerState.unavailable,
};

extension ServerStateView on ServerState {
  String get label => switch (this) {
    ServerState.available => 'Доступен',
    ServerState.busy => 'Занят',
    ServerState.unavailable => 'Недоступен',
  };
  Color color(OrbitTokens t) => switch (this) {
    ServerState.available => t.success,
    ServerState.busy => t.warning,
    ServerState.unavailable => t.idle,
  };
}

class StatusDot extends StatelessWidget {
  const StatusDot(this.color, {super.key, this.size = 8});
  final Color color;
  final double size;
  @override
  Widget build(BuildContext context) => Container(
    width: size,
    height: size,
    decoration: BoxDecoration(color: color, shape: BoxShape.circle),
  );
}

/// Dot + short label ("Доступен", "Занят", …).
class StateLabel extends StatelessWidget {
  const StateLabel({super.key, required this.label, required this.color});
  final String label;
  final Color color;
  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      StatusDot(color),
      const SizedBox(width: 8),
      Flexible(
        child: Text(label, style: Theme.of(context).textTheme.bodySmall),
      ),
    ],
  );
}

/// ADR-102: a measured round trip ("42 мс"), "нет ответа", or "измеряем…" while pending.
class LatencyLabel extends StatelessWidget {
  const LatencyLabel({super.key, required this.ms, this.measuring = false});
  final int? ms;
  final bool measuring;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final color = measuring
        ? t.idle
        : switch (latencyBand(ms)) {
            LatencyBand.good => t.success,
            LatencyBand.fair => t.warning,
            LatencyBand.poor => t.danger,
          };
    return Semantics(
      label: measuring ? 'Пинг измеряется' : 'Пинг ${latencyLabel(ms)}',
      excludeSemantics: true,
      child: StateLabel(
        label: measuring ? 'измеряем…' : latencyLabel(ms),
        color: color,
      ),
    );
  }
}

class OrbitBadge extends StatelessWidget {
  const OrbitBadge(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: t.surface3,
        borderRadius: BorderRadius.circular(OrbitTokens.radiusS),
        border: Border.all(color: t.outline),
      ),
      child: Text(
        text,
        style: Theme.of(context).textTheme.bodySmall?.copyWith(color: t.text),
      ),
    );
  }
}

/// Rounded square holding a line icon.
class IconTile extends StatelessWidget {
  const IconTile(this.icon, {super.key, this.color});
  final IconData icon;
  final Color? color;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final c = color ?? t.accent;
    return Container(
      width: 44,
      height: 44,
      decoration: BoxDecoration(
        color: c.withValues(alpha: .14),
        borderRadius: BorderRadius.circular(OrbitTokens.radiusS),
      ),
      child: Icon(icon, color: c, size: 22),
    );
  }
}

/// Card row: icon tile, title, optional subtitle and a chevron.
class NavRow extends StatelessWidget {
  const NavRow({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.onTap,
    this.trailing,
    this.leading,
    this.enabled = true,
  });
  final IconData icon;
  final String title;
  final String? subtitle;
  final VoidCallback? onTap;
  final Widget? trailing;

  /// Replaces the icon tile (for example with a country flag).
  final Widget? leading;
  final bool enabled;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    return Opacity(
      opacity: enabled ? 1 : .5,
      child: OrbitPanel(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        onTap: enabled ? onTap : null,
        child: Row(
          children: [
            leading ?? IconTile(icon),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: Theme.of(context).textTheme.titleMedium),
                  if (subtitle != null) ...[
                    const SizedBox(height: 2),
                    Text(
                      subtitle!,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ],
              ),
            ),
            trailing ??
                (onTap == null
                    ? const SizedBox.shrink()
                    : Icon(Icons.chevron_right_rounded, color: t.textMuted)),
          ],
        ),
      ),
    );
  }
}

/// Icon + title + subtitle at the top of a tab or page.
class ScreenHeader extends StatelessWidget {
  const ScreenHeader({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.trailing,
  });
  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: OrbitTokens.space3),
      child: Row(
        children: [
          Icon(icon, size: 32, color: t.accent),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: Theme.of(context).textTheme.headlineSmall),
                if (subtitle != null)
                  Text(subtitle!, style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}

/// Rounded progress bar with the lavender gradient.
class UsageBar extends StatelessWidget {
  const UsageBar({super.key, required this.value, this.height = 8});
  final double value;
  final double height;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final v = value.isNaN ? 0.0 : value.clamp(0.0, 1.0);
    return ClipRRect(
      borderRadius: BorderRadius.circular(height),
      child: SizedBox(
        height: height,
        child: Stack(
          children: [
            Positioned.fill(child: ColoredBox(color: t.surface3)),
            TweenAnimationBuilder<double>(
              tween: Tween(begin: 0, end: v),
              duration: OrbitMotion.of(
                context,
                const Duration(milliseconds: 900),
              ),
              curve: OrbitMotion.emphasized,
              builder: (context, value, child) =>
                  FractionallySizedBox(widthFactor: value, child: child),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(height),
                  gradient: LinearGradient(
                    colors: [t.accent, Color.lerp(t.accent, t.cta, .35)!],
                  ),
                ),
                child: const SizedBox.expand(),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Ring gauge for the traffic tab.
class DonutGauge extends StatelessWidget {
  const DonutGauge({
    super.key,
    required this.value,
    required this.child,
    this.size = 140,
  });
  final double value;
  final Widget child;
  final double size;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    return SizedBox.square(
      dimension: size,
      child: TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: value.isNaN ? 0 : value.clamp(0.0, 1.0)),
        duration: OrbitMotion.of(context, const Duration(milliseconds: 1100)),
        curve: OrbitMotion.emphasized,
        builder: (context, v, child) => CustomPaint(
          painter: _DonutPainter(v, t.surface3, t.accent, t.cta),
          child: child,
        ),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Center(
            child: FittedBox(fit: BoxFit.scaleDown, child: child),
          ),
        ),
      ),
    );
  }
}

class _DonutPainter extends CustomPainter {
  _DonutPainter(this.value, this.track, this.from, this.to);
  final double value;
  final Color track, from, to;
  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 12.0;
    final rect = (Offset.zero & size).deflate(stroke / 2);
    canvas.drawArc(
      rect,
      0,
      2 * math.pi,
      false,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke
        ..color = track,
    );
    if (value <= 0) return;
    canvas.drawArc(
      rect,
      -math.pi / 2,
      2 * math.pi * value,
      false,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke
        ..strokeCap = StrokeCap.round
        ..shader = SweepGradient(
          startAngle: 0,
          endAngle: 2 * math.pi,
          colors: [from, Color.lerp(from, to, .5)!, from],
          transform: const GradientRotation(-math.pi / 2),
        ).createShader(rect),
    );
  }

  @override
  bool shouldRepaint(_DonutPainter old) =>
      old.value != value || old.track != track || old.from != from;
}

/// Inline notice pill (warning or error) with an icon.
class OrbitNotice extends StatelessWidget {
  const OrbitNotice({
    super.key,
    required this.text,
    this.danger = false,
    this.child,
  });
  final String text;
  final bool danger;
  final Widget? child;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final c = danger ? t.danger : t.warning;
    return Semantics(
      liveRegion: true,
      child: OrbitPanel(
        tone: c,
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  danger ? Icons.error_outline_rounded : Icons.info_outline,
                  color: c,
                  size: 22,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    text,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ),
              ],
            ),
            ?child,
          ],
        ),
      ),
    );
  }
}

/// Outlined destructive button ("Отключить", "Отменить").
ButtonStyle dangerOutlined(BuildContext context) {
  final t = OrbitTokens.of(context);
  return OutlinedButton.styleFrom(
    foregroundColor: t.danger,
    side: BorderSide(color: t.danger, width: 1.5),
    minimumSize: const Size(48, 56),
  );
}

/// "Detour" + lavender "Vpn" wordmark.
class OrbitLogo extends StatelessWidget {
  const OrbitLogo({super.key});
  @override
  Widget build(BuildContext context) => Text.rich(
    TextSpan(
      children: [
        const TextSpan(
          text: 'Detour',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
        TextSpan(
          text: 'Vpn',
          style: TextStyle(
            fontWeight: FontWeight.w600,
            color: OrbitTokens.of(context).accent,
          ),
        ),
      ],
    ),
    style: Theme.of(context).textTheme.titleLarge?.copyWith(fontSize: 22),
  );
}

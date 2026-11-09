import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'ui/motion.dart';
import 'ui/tokens.dart';

/// Visual connection state of the orbit. Decorative only: the orbit never
/// represents a measurement (latency, speed or tunnel health).
enum OrbitState { idle, connecting, connected, error }

/// Asset of one orbit state; artwork is aligned so every state shares the
/// same body position (see docs/design/soft-orbit-screen-map.md).
String orbitAsset(OrbitState state, {required bool dark}) =>
    'assets/orbit/orbit_${dark ? 'dark' : 'light'}_${state.name}.webp';

/// The Soft Orbit glass blob (raster artwork, 4:3). States cross-fade; the
/// content sits over the glass body. Without a [child] the blob shows the
/// state icon; only the connecting ring rotates.
class OrbitBlob extends StatefulWidget {
  const OrbitBlob({
    super.key,
    this.state = OrbitState.idle,
    this.height = 220,
    this.child,
  });
  final OrbitState state;

  /// Preferred artwork height; narrower screens scale it down.
  final double height;
  final Widget? child;

  @override
  State<OrbitBlob> createState() => _OrbitBlobState();
}

class _OrbitBlobState extends State<OrbitBlob>
    with SingleTickerProviderStateMixin {
  late final AnimationController _spin = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  );
  bool? _cachedDark;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final dark = Theme.of(context).brightness == Brightness.dark;
    if (_cachedDark != dark) {
      _cachedDark = dark;
      // Decode every state up front so the first switch does not flash.
      for (final state in OrbitState.values) {
        precacheImage(AssetImage(orbitAsset(state, dark: dark)), context);
      }
    }
    _sync();
  }

  @override
  void didUpdateWidget(OrbitBlob oldWidget) {
    super.didUpdateWidget(oldWidget);
    _sync();
  }

  void _sync() {
    final animate =
        widget.child == null &&
        widget.state == OrbitState.connecting &&
        !OrbitMotion.reduced(context);
    if (animate && !_spin.isAnimating) {
      _spin.repeat();
    } else if (!animate && _spin.isAnimating) {
      _spin.stop();
    }
  }

  @override
  void dispose() {
    _spin.dispose();
    super.dispose();
  }

  Widget _icon(OrbitTokens t, bool dark) {
    final color = switch (widget.state) {
      OrbitState.error => dark ? const Color(0xffff8f9b) : t.danger,
      OrbitState.connected => dark ? t.text : t.cta,
      _ => t.accent,
    };
    const size = 52.0;
    final icon = switch (widget.state) {
      OrbitState.idle => Icon(
        Icons.power_settings_new_rounded,
        size: size,
        color: color,
      ),
      OrbitState.connecting => RotationTransition(
        turns: _spin,
        child: CustomPaint(
          size: const Size.square(size),
          painter: _DashedRing(color),
        ),
      ),
      OrbitState.connected => Icon(
        Icons.check_circle_outline_rounded,
        size: size,
        color: color,
      ),
      OrbitState.error => Icon(
        Icons.error_outline_rounded,
        size: size,
        color: color,
      ),
    };
    return KeyedSubtree(key: ValueKey(widget.state), child: icon);
  }

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final dark = Theme.of(context).brightness == Brightness.dark;
    final fade = OrbitMotion.of(context, const Duration(milliseconds: 600));
    return LayoutBuilder(
      builder: (context, box) {
        final width = math.min(box.maxWidth, widget.height * 4 / 3);
        final height = width * 3 / 4;
        return Center(
          child: SizedBox(
            width: width,
            height: height,
            child: Stack(
              fit: StackFit.expand,
              children: [
                ExcludeSemantics(
                  child: AnimatedSwitcher(
                    duration: fade,
                    switchInCurve: OrbitMotion.emphasized,
                    switchOutCurve: Curves.easeIn,
                    layoutBuilder: (current, previous) => Stack(
                      fit: StackFit.expand,
                      children: [...previous, ?current],
                    ),
                    child: Image.asset(
                      orbitAsset(widget.state, dark: dark),
                      key: ValueKey('${widget.state.name}-$dark'),
                      fit: BoxFit.contain,
                      filterQuality: FilterQuality.medium,
                      gaplessPlayback: true,
                    ),
                  ),
                ),
                // The glass body spans roughly 17–83 % × 18–76 % of the art.
                Positioned(
                  left: width * .22,
                  right: width * .22,
                  top: height * .24,
                  bottom: height * .26,
                  child: Center(
                    child: FittedBox(
                      fit: BoxFit.scaleDown,
                      child: widget.child == null
                          ? AnimatedSwitcher(
                              duration: OrbitMotion.of(
                                context,
                                OrbitMotion.medium,
                              ),
                              transitionBuilder: (child, animation) =>
                                  FadeTransition(
                                    opacity: animation,
                                    child: ScaleTransition(
                                      scale: Tween(
                                        begin: .8,
                                        end: 1.0,
                                      ).animate(animation),
                                      child: child,
                                    ),
                                  ),
                              child: _icon(t, dark),
                            )
                          : widget.child,
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Dashed "connecting" ring drawn over the glass.
class _DashedRing extends CustomPainter {
  _DashedRing(this.color);
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    const dashes = 10;
    const sweep = 2 * math.pi / dashes;
    final rect = Offset.zero & size;
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4.5
      ..strokeCap = StrokeCap.round;
    for (var i = 0; i < dashes; i++) {
      paint.color = color.withValues(alpha: .35 + .65 * (i / (dashes - 1)));
      canvas.drawArc(rect.deflate(4), i * sweep, sweep * .45, false, paint);
    }
  }

  @override
  bool shouldRepaint(_DashedRing old) => old.color != color;
}

/// Soft background glows behind every screen.
class OrbitBackdrop extends StatelessWidget {
  const OrbitBackdrop({super.key, required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(color: t.bg),
      child: Stack(
        children: [
          Positioned(
            top: -120,
            right: -100,
            child: _Glow(color: t.accent.withValues(alpha: .10), size: 320),
          ),
          Positioned(
            bottom: -140,
            left: -120,
            child: _Glow(color: t.cta.withValues(alpha: .06), size: 340),
          ),
          Positioned.fill(child: child),
        ],
      ),
    );
  }
}

class _Glow extends StatelessWidget {
  const _Glow({required this.color, required this.size});
  final Color color;
  final double size;
  @override
  Widget build(BuildContext context) => IgnorePointer(
    child: Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: RadialGradient(colors: [color, color.withValues(alpha: 0)]),
      ),
    ),
  );
}

/// Glass card: `surface-1`, 1 px outline, `radius-l`. A selected card gets an
/// accent border and glow; both animate, and a tappable card sinks slightly
/// while pressed.
class OrbitPanel extends StatefulWidget {
  const OrbitPanel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.onTap,
    this.selected = false,
    this.tone,
  });
  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final bool selected;

  /// Optional tint for notices (warning/danger).
  final Color? tone;

  @override
  State<OrbitPanel> createState() => _OrbitPanelState();
}

class _OrbitPanelState extends State<OrbitPanel> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final radius = BorderRadius.circular(OrbitTokens.radiusL);
    final tone = widget.tone;
    final selected = widget.selected;
    final border = selected
        ? t.accent
        : tone?.withValues(alpha: .45) ?? t.outline;
    final d = OrbitMotion.of(context, OrbitMotion.medium);
    return AnimatedScale(
      scale: _pressed ? .98 : 1,
      duration: OrbitMotion.of(context, OrbitMotion.fast),
      curve: Curves.easeOut,
      child: AnimatedContainer(
        duration: d,
        curve: OrbitMotion.emphasized,
        decoration: BoxDecoration(
          borderRadius: radius,
          border: Border.all(color: border, width: selected ? 1.5 : 1),
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: tone == null
                ? [t.surface2, t.surface1]
                : [
                    Color.alphaBlend(tone.withValues(alpha: .16), t.surface1),
                    Color.alphaBlend(tone.withValues(alpha: .06), t.surface1),
                  ],
          ),
          boxShadow: [
            BoxShadow(
              color: selected ? t.accent.withValues(alpha: .24) : t.shadow,
              blurRadius: 24,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: Material(
          type: MaterialType.transparency,
          borderRadius: radius,
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: widget.onTap,
            onHighlightChanged: widget.onTap == null
                ? null
                : (value) => setState(() => _pressed = value),
            child: Padding(
              padding: widget.padding,
              child: SizedBox(width: double.infinity, child: widget.child),
            ),
          ),
        ),
      ),
    );
  }
}

/// A compact display value; accounting still uses integer bytes exclusively.
String compactBytes(int bytes) {
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ', 'ПБ', 'ЭБ'];
  var divisor = 1;
  var unit = 0;
  while (unit < units.length - 1 && bytes.abs() ~/ divisor >= 1000) {
    divisor *= 1000;
    unit++;
  }
  if (unit == 0) return '$bytes Б';
  final tenths = bytes.abs() ~/ (divisor ~/ 10);
  return '${bytes < 0 ? '−' : ''}${tenths ~/ 10},${tenths % 10} ${units[unit]}';
}

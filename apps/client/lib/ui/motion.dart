import 'dart:math' as math;

import 'package:flutter/material.dart';

/// Motion tokens. Every animation is finite except the "connecting" orbit arc,
/// and all of them collapse to zero when the system asks to reduce motion.
class OrbitMotion {
  static const fast = Duration(milliseconds: 180);
  static const medium = Duration(milliseconds: 280);
  static const slow = Duration(milliseconds: 450);
  static const emphasized = Cubic(0.2, 0, 0, 1);

  static bool reduced(BuildContext context) =>
      MediaQuery.maybeDisableAnimationsOf(context) ?? false;

  static Duration of(BuildContext context, Duration value) =>
      reduced(context) ? Duration.zero : value;
}

/// Pushed pages: fade + short horizontal slide; the page below dims slightly.
class OrbitPageTransitionsBuilder extends PageTransitionsBuilder {
  const OrbitPageTransitionsBuilder();

  @override
  Duration get transitionDuration => const Duration(milliseconds: 360);

  @override
  Duration get reverseTransitionDuration => const Duration(milliseconds: 280);

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    final enter = CurvedAnimation(
      parent: animation,
      curve: OrbitMotion.emphasized,
      reverseCurve: Curves.easeInCubic,
    );
    final below = CurvedAnimation(
      parent: secondaryAnimation,
      curve: Curves.easeOutCubic,
    );
    return FadeTransition(
      opacity: Tween<double>(begin: 1, end: .55).animate(below),
      child: FadeTransition(
        opacity: enter,
        child: SlideTransition(
          position: Tween(
            begin: const Offset(.08, 0),
            end: Offset.zero,
          ).animate(enter),
          child: child,
        ),
      ),
    );
  }
}

/// Fade-through for swapped content (tabs, login steps, button states).
class OrbitSwitcher extends StatelessWidget {
  const OrbitSwitcher({
    super.key,
    required this.child,
    this.duration = OrbitMotion.medium,
    this.offset = .03,
    this.alignment = Alignment.topCenter,
  });
  final Widget child;
  final Duration duration;
  final double offset;
  final Alignment alignment;

  @override
  Widget build(BuildContext context) => AnimatedSwitcher(
    duration: OrbitMotion.of(context, duration),
    switchInCurve: OrbitMotion.emphasized,
    switchOutCurve: Curves.easeInCubic,
    layoutBuilder: (current, previous) => Stack(
      alignment: alignment,
      fit: StackFit.passthrough,
      children: [...previous, ?current],
    ),
    transitionBuilder: (child, animation) => FadeTransition(
      opacity: animation,
      child: SlideTransition(
        position: Tween(
          begin: Offset(0, offset),
          end: Offset.zero,
        ).animate(animation),
        child: ScaleTransition(
          scale: Tween<double>(begin: .985, end: 1).animate(animation),
          child: child,
        ),
      ),
    ),
    child: child,
  );
}

/// One-shot entrance: fade + rise, staggered by [index]. No timers are used,
/// so a test or a fast tab switch never leaves anything pending.
class OrbitAppear extends StatefulWidget {
  const OrbitAppear({super.key, required this.child, this.index = 0});
  final Widget child;
  final int index;

  @override
  State<OrbitAppear> createState() => _OrbitAppearState();
}

class _OrbitAppearState extends State<OrbitAppear>
    with SingleTickerProviderStateMixin {
  static const _step = 45;
  static const _base = 380;
  late final int _delay = math.min(widget.index, 8) * _step;
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: Duration(milliseconds: _base + _delay),
  );
  late final Animation<double> _t = CurvedAnimation(
    parent: _c,
    curve: Interval(
      _delay / (_base + _delay),
      1,
      curve: OrbitMotion.emphasized,
    ),
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_c.status == AnimationStatus.dismissed) {
      if (OrbitMotion.reduced(context)) {
        _c.value = 1;
      } else {
        _c.forward();
      }
    }
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: _t,
    builder: (context, child) => Opacity(
      opacity: _t.value,
      child: Transform.translate(
        offset: Offset(0, 14 * (1 - _t.value)),
        child: child,
      ),
    ),
    child: widget.child,
  );
}

/// Wraps a list of children so each one rises in after the previous one.
List<Widget> staggered(List<Widget> children) => [
  for (final (i, child) in children.indexed)
    OrbitAppear(index: i, child: child),
];

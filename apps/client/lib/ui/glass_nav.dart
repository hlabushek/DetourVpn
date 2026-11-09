import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import 'motion.dart';
import 'tokens.dart';

class GlassNavItem {
  const GlassNavItem({
    required this.icon,
    required this.selectedIcon,
    required this.label,
  });
  final IconData icon;
  final IconData selectedIcon;
  final String label;
}

/// Space the floating bar takes at the bottom of the screen, so scrolling
/// content can pass under the glass and still end above it.
class GlassNavInset extends InheritedWidget {
  const GlassNavInset({super.key, required this.inset, required super.child});
  final double inset;

  static double of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<GlassNavInset>()?.inset ?? 0;

  @override
  bool updateShouldNotify(GlassNavInset oldWidget) => oldWidget.inset != inset;
}

/// "Liquid glass" tab bar: a floating frosted pill (backdrop blur, tint,
/// specular edge) with a glass lens that flows to the selected tab and
/// stretches while it moves. Drawn entirely in Flutter; no platform API.
class GlassNavBar extends StatefulWidget {
  const GlassNavBar({
    super.key,
    required this.items,
    required this.selectedIndex,
    required this.onSelected,
  });
  final List<GlassNavItem> items;
  final int selectedIndex;
  final ValueChanged<int> onSelected;

  static const height = 68.0;
  static const margin = 12.0;

  /// Total bottom inset (bar + margin + system gesture area).
  static double insetOf(BuildContext context) =>
      height + margin * 2 + MediaQuery.paddingOf(context).bottom;

  @override
  State<GlassNavBar> createState() => _GlassNavBarState();
}

class _GlassNavBarState extends State<GlassNavBar>
    with SingleTickerProviderStateMixin {
  late final AnimationController _move = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 520),
    value: 1,
  );
  late double _from = widget.selectedIndex.toDouble();

  @override
  void didUpdateWidget(GlassNavBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.selectedIndex != widget.selectedIndex) {
      _from = _position;
      if (OrbitMotion.reduced(context)) {
        _move.value = 1;
      } else {
        _move.forward(from: 0);
      }
    }
  }

  double get _t => OrbitMotion.emphasized.transform(_move.value);
  double get _position =>
      ui.lerpDouble(_from, widget.selectedIndex.toDouble(), _t)!;

  @override
  void dispose() {
    _move.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final dark = Theme.of(context).brightness == Brightness.dark;
    final radius = BorderRadius.circular(GlassNavBar.height / 2);
    return SafeArea(
      top: false,
      minimum: const EdgeInsets.fromLTRB(16, 0, 16, GlassNavBar.margin),
      child: Center(
        heightFactor: 1,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 440),
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: radius,
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: dark ? .38 : .12),
                  blurRadius: 28,
                  offset: const Offset(0, 12),
                ),
              ],
            ),
            child: ClipRRect(
              borderRadius: radius,
              child: BackdropFilter(
                filter: ui.ImageFilter.blur(sigmaX: 22, sigmaY: 22),
                child: Container(
                  height: GlassNavBar.height,
                  decoration: BoxDecoration(
                    borderRadius: radius,
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [
                        t.surface2.withValues(alpha: dark ? .62 : .66),
                        t.surface1.withValues(alpha: dark ? .44 : .52),
                      ],
                    ),
                    border: Border.all(
                      color: Colors.white.withValues(alpha: dark ? .14 : .7),
                    ),
                  ),
                  child: LayoutBuilder(
                    builder: (context, box) {
                      final slot = box.maxWidth / widget.items.length;
                      return Stack(
                        children: [
                          // Specular edge along the top of the glass.
                          Positioned(
                            top: 1,
                            left: 28,
                            right: 28,
                            height: 1,
                            child: DecoratedBox(
                              decoration: BoxDecoration(
                                gradient: LinearGradient(
                                  colors: [
                                    Colors.white.withValues(alpha: 0),
                                    Colors.white.withValues(
                                      alpha: dark ? .35 : .9,
                                    ),
                                    Colors.white.withValues(alpha: 0),
                                  ],
                                ),
                              ),
                            ),
                          ),
                          AnimatedBuilder(
                            animation: _move,
                            builder: (context, _) {
                              // Stretch peaks mid-flight, like a drop of liquid.
                              final stretch = math.sin(math.pi * _t) * .28;
                              final width = (slot - 12) * (1 + stretch);
                              final left =
                                  _position * slot + (slot - width) / 2;
                              return Positioned(
                                left: left,
                                top: 6,
                                bottom: 6,
                                width: width,
                                child: _Lens(tokens: t, dark: dark),
                              );
                            },
                          ),
                          Row(
                            children: [
                              for (final (i, item) in widget.items.indexed)
                                Expanded(
                                  child: _NavButton(
                                    item: item,
                                    selected: i == widget.selectedIndex,
                                    onTap: () => widget.onSelected(i),
                                  ),
                                ),
                            ],
                          ),
                        ],
                      );
                    },
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Lens extends StatelessWidget {
  const _Lens({required this.tokens, required this.dark});
  final OrbitTokens tokens;
  final bool dark;
  @override
  Widget build(BuildContext context) => DecoratedBox(
    decoration: BoxDecoration(
      borderRadius: BorderRadius.circular(GlassNavBar.height / 2),
      gradient: LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          Color.alphaBlend(
            Colors.white.withValues(alpha: dark ? .10 : .55),
            tokens.accent.withValues(alpha: dark ? .26 : .16),
          ),
          tokens.accent.withValues(alpha: dark ? .12 : .08),
        ],
      ),
      border: Border.all(
        color: Colors.white.withValues(alpha: dark ? .22 : .8),
      ),
      boxShadow: [
        BoxShadow(
          color: tokens.accent.withValues(alpha: dark ? .30 : .18),
          blurRadius: 18,
        ),
      ],
    ),
  );
}

class _NavButton extends StatelessWidget {
  const _NavButton({
    required this.item,
    required this.selected,
    required this.onTap,
  });
  final GlassNavItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final color = selected ? t.accent : t.textMuted;
    final d = OrbitMotion.of(context, OrbitMotion.medium);
    return Semantics(
      button: true,
      selected: selected,
      child: InkResponse(
        onTap: onTap,
        radius: 36,
        highlightShape: BoxShape.rectangle,
        customBorder: const StadiumBorder(),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            AnimatedScale(
              scale: selected ? 1.12 : 1,
              duration: d,
              curve: Curves.easeOutBack,
              child: AnimatedSwitcher(
                duration: d,
                child: Icon(
                  selected ? item.selectedIcon : item.icon,
                  key: ValueKey(selected),
                  color: color,
                  size: 24,
                ),
              ),
            ),
            const SizedBox(height: 2),
            AnimatedDefaultTextStyle(
              duration: d,
              style: Theme.of(context).textTheme.labelMedium!.copyWith(
                fontSize: 12,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
                color: color,
              ),
              child: Text(
                item.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

import 'package:flutter/material.dart';

enum OrbitAccent { coral, lavender, turquoise, gold }

Color orbitWarning(Brightness brightness) => brightness == Brightness.dark
    ? const Color(0xffffda8b)
    : const Color(0xff744500);

/// Shared tokens; applying these does not change tunnel behavior or navigation.
ThemeData orbitTheme(
  Brightness brightness, {
  OrbitAccent accent = OrbitAccent.coral,
}) {
  final dark = brightness == Brightness.dark;
  final seed = switch (accent) {
    OrbitAccent.coral => const Color(0xffb34f43),
    OrbitAccent.lavender => const Color(0xff7753a5),
    OrbitAccent.turquoise => const Color(0xff217b79),
    OrbitAccent.gold => const Color(0xff896417),
  };
  final scheme = ColorScheme.fromSeed(seedColor: seed, brightness: brightness)
      .copyWith(
        surface: dark ? const Color(0xff241c2a) : const Color(0xfffff7fc),
        onSurface: dark ? const Color(0xfff4e9f0) : const Color(0xff302334),
        error: dark ? const Color(0xffffb4ab) : const Color(0xffba1a1a),
        onError: dark ? const Color(0xff690005) : Colors.white,
      );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    cardTheme: CardThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(28)),
    ),
  );
}

class OrbitRoute extends StatelessWidget {
  const OrbitRoute({super.key});
  @override
  Widget build(BuildContext context) => ExcludeSemantics(
    child: SizedBox(
      height: 48,
      child: CustomPaint(
        painter: _RoutePainter(Theme.of(context).colorScheme.primary),
      ),
    ),
  );
}

class _RoutePainter extends CustomPainter {
  const _RoutePainter(this.color);
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final line = Paint()
      ..color = color.withValues(alpha: 0.35)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;
    final path = Path()
      ..moveTo(8, 28)
      ..cubicTo(
        size.width * 0.25,
        -4,
        size.width * 0.45,
        60,
        size.width * 0.7,
        24,
      )
      ..quadraticBezierTo(size.width * 0.85, 4, size.width - 8, 20);
    canvas.drawPath(path, line);
    canvas.drawCircle(const Offset(8, 28), 4, Paint()..color = color);
    canvas.drawCircle(Offset(size.width - 8, 20), 4, Paint()..color = color);
  }

  @override
  bool shouldRepaint(_RoutePainter oldDelegate) => oldDelegate.color != color;
}

import 'package:flutter/material.dart';

enum OrbitAccent { coral, lavender, turquoise, gold }

Color orbitWarning(Brightness brightness) => brightness == Brightness.dark
    ? const Color(0xffffda8b)
    : const Color(0xff744500);

/// Shared tokens; applying these does not change tunnel behavior or navigation.
ThemeData orbitTheme(
  Brightness brightness, {
  OrbitAccent accent = OrbitAccent.lavender,
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
        surface: dark ? const Color(0xff211b30) : const Color(0xfffaf5ff),
        onSurface: dark ? const Color(0xfff4e9f0) : const Color(0xff302334),
        error: dark ? const Color(0xffffb4ab) : const Color(0xffba1a1a),
        onError: dark ? const Color(0xff690005) : Colors.white,
      );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: dark
        ? const Color(0xff191624)
        : const Color(0xfffaf5ff),
    appBarTheme: const AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: dark ? const Color(0xff241e33) : Colors.white,
      indicatorColor: seed.withValues(alpha: dark ? 0.28 : 0.14),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: dark
            ? const Color(0xffff7e88)
            : const Color(0xffa53649),
        foregroundColor: dark ? const Color(0xff30101b) : Colors.white,
        minimumSize: const Size(48, 52),
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
        textStyle: const TextStyle(
          fontFamily: 'Roboto',
          fontWeight: FontWeight.w600,
          fontSize: 16,
        ),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surfaceContainerHigh,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(20)),
    ),
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

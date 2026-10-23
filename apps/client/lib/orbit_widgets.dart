import 'package:flutter/material.dart';

/// Decorative only: orbit positions never represent connection measurements.
class OrbitArtwork extends StatelessWidget {
  const OrbitArtwork({super.key, this.child});
  final Widget? child;

  @override
  Widget build(BuildContext context) => CustomPaint(
    painter: _OrbitPainter(Theme.of(context).brightness == Brightness.dark),
    child: Padding(
      padding: const EdgeInsets.fromLTRB(28, 78, 28, 20),
      child: child ?? const SizedBox(height: 60, width: double.infinity),
    ),
  );
}

class _OrbitPainter extends CustomPainter {
  _OrbitPainter(this.dark);
  final bool dark;
  @override
  void paint(Canvas canvas, Size size) {
    final bounds = Offset.zero & size;
    final shape = Path()
      ..moveTo(size.width * .07, size.height * .6)
      ..cubicTo(-8, 75, size.width * .36, 25, size.width * .56, 20)
      ..cubicTo(
        size.width * .94,
        -10,
        size.width * 1.1,
        size.height * .56,
        size.width * .88,
        size.height * .88,
      )
      ..cubicTo(
        size.width * .72,
        size.height * 1.04,
        size.width * .16,
        size.height * .99,
        size.width * .07,
        size.height * .6,
      );
    canvas.drawPath(
      shape,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topRight,
          end: Alignment.bottomLeft,
          colors: [
            const Color(0xffff8793).withValues(alpha: dark ? .35 : .2),
            const Color(0xffaa7bef).withValues(alpha: .08),
          ],
        ).createShader(bounds),
    );
    final line = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..shader = const LinearGradient(
        colors: [Color(0xffb88aff), Color(0xffff939c)],
      ).createShader(bounds);
    canvas.drawPath(shape, line);
    final orbit = Path()
      ..moveTo(size.width * .04, size.height * .72)
      ..cubicTo(-5, 20, size.width * .32, 85, size.width * .5, 38)
      ..cubicTo(
        size.width * .81,
        -25,
        size.width * .9,
        100,
        size.width * .96,
        size.height * .55,
      );
    canvas.drawPath(orbit, line);
    final center = Offset(size.width * .68, 65);
    canvas.drawCircle(
      center,
      13,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment.topLeft,
          colors: [Color(0xffe6ceff), Color(0xff704d9c)],
        ).createShader(Rect.fromCircle(center: center, radius: 13)),
    );
  }

  @override
  bool shouldRepaint(_OrbitPainter oldDelegate) => oldDelegate.dark != dark;
}

class OrbitPanel extends StatelessWidget {
  const OrbitPanel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
  });
  final Widget child;
  final EdgeInsetsGeometry padding;
  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: padding,
    decoration: BoxDecoration(
      borderRadius: BorderRadius.circular(26),
      border: Border.all(
        color: Theme.of(
          context,
        ).colorScheme.outlineVariant.withValues(alpha: .5),
      ),
      gradient: LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [
          Theme.of(context).colorScheme.surfaceContainerHigh,
          Theme.of(context).colorScheme.surface,
        ],
      ),
    ),
    child: child,
  );
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

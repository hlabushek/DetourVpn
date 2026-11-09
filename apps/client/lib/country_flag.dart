import 'package:flutter/material.dart';

/// Country flags drawn from simple geometry, so the app needs no image assets,
/// no emoji font and no extra dependency. Unknown codes fall back to a globe.
/// Mirror of apps/telegram-mini-app/src/flags.mjs; keep both tables in sync.
sealed class FlagSpec {
  const FlagSpec();
}

class StripesFlag extends FlagSpec {
  const StripesFlag(this.colors, {this.vertical = false});
  final List<int> colors;
  final bool vertical;
}

class NordicCrossFlag extends FlagSpec {
  const NordicCrossFlag(this.field, this.cross, {this.inner});
  final int field;
  final int cross;
  final int? inner;
}

const Map<String, FlagSpec> flagSpecs = {
  'FI': NordicCrossFlag(0xFFFFFFFF, 0xFF003580),
  'SE': NordicCrossFlag(0xFF006AA7, 0xFFFECC00),
  'DK': NordicCrossFlag(0xFFC8102E, 0xFFFFFFFF),
  'NO': NordicCrossFlag(0xFFBA0C2F, 0xFFFFFFFF, inner: 0xFF00205B),
  'DE': StripesFlag([0xFF000000, 0xFFDD0000, 0xFFFFCE00]),
  'NL': StripesFlag([0xFFAE1C28, 0xFFFFFFFF, 0xFF21468B]),
  'AT': StripesFlag([0xFFED2939, 0xFFFFFFFF, 0xFFED2939]),
  'PL': StripesFlag([0xFFFFFFFF, 0xFFDC143C]),
  'EE': StripesFlag([0xFF0072CE, 0xFF000000, 0xFFFFFFFF]),
  'LT': StripesFlag([0xFFFDB913, 0xFF006A44, 0xFFC1272D]),
  'FR': StripesFlag([0xFF0055A4, 0xFFFFFFFF, 0xFFEF4135], vertical: true),
  'IT': StripesFlag([0xFF009246, 0xFFFFFFFF, 0xFFCE2B37], vertical: true),
};

/// Russian display names for the same set; mirror of `COUNTRY_NAMES` in flags.mjs.
const Map<String, String> countryNames = {
  'FI': 'Финляндия',
  'SE': 'Швеция',
  'DK': 'Дания',
  'NO': 'Норвегия',
  'DE': 'Германия',
  'NL': 'Нидерланды',
  'AT': 'Австрия',
  'PL': 'Польша',
  'EE': 'Эстония',
  'LT': 'Литва',
  'FR': 'Франция',
  'IT': 'Италия',
};

/// Accepts only an ISO 3166-1 alpha-2 code in upper case; anything else is
/// treated as unknown rather than rejecting the whole node list.
String? parseCountryCode(Object? value) =>
    value is String && RegExp(r'^[A-Z]{2}$').hasMatch(value) ? value : null;

/// Free-text city from the API, bounded for display.
String? parseCity(Object? value) =>
    value is String && value.isNotEmpty && value.length <= 64 ? value : null;

class CountryFlag extends StatelessWidget {
  const CountryFlag(this.code, {super.key, this.width = 28});
  final String? code;
  final double width;

  @override
  Widget build(BuildContext context) {
    final spec = code == null ? null : flagSpecs[code];
    final height = width * 0.7;
    return Semantics(
      label: code == null ? 'Страна не указана' : 'Флаг $code',
      excludeSemantics: true,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(width * 0.12),
        child: SizedBox(
          width: width,
          height: height,
          child: spec == null
              ? ColoredBox(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  child: Icon(
                    Icons.public,
                    size: height * 0.8,
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                )
              : CustomPaint(painter: _FlagPainter(spec)),
        ),
      ),
    );
  }
}

class _FlagPainter extends CustomPainter {
  const _FlagPainter(this.spec);
  final FlagSpec spec;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..isAntiAlias = false;
    void rect(double l, double t, double r, double b, int color) {
      paint.color = Color(color);
      canvas.drawRect(Rect.fromLTRB(l, t, r, b), paint);
    }

    final w = size.width, h = size.height;
    switch (spec) {
      case StripesFlag(:final colors, :final vertical):
        final n = colors.length;
        for (var i = 0; i < n; i++) {
          if (vertical) {
            rect(w * i / n, 0, w * (i + 1) / n, h, colors[i]);
          } else {
            rect(0, h * i / n, w, h * (i + 1) / n, colors[i]);
          }
        }
      case NordicCrossFlag(:final field, :final cross, :final inner):
        rect(0, 0, w, h, field);
        final bar = h * 0.3;
        final x = w * 0.31, y = (h - bar) / 2;
        rect(x, 0, x + bar, h, cross);
        rect(0, y, w, y + bar, cross);
        if (inner != null) {
          final thin = bar * 0.5, dx = (bar - thin) / 2;
          rect(x + dx, 0, x + dx + thin, h, inner);
          rect(0, y + dx, w, y + dx + thin, inner);
        }
    }
  }

  @override
  bool shouldRepaint(_FlagPainter old) => old.spec != spec;
}

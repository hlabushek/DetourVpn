import 'package:flutter/material.dart';

/// Soft Orbit tokens; names mirror docs/design/soft-orbit-tokens.md and the
/// Mini App CSS custom properties (`surface2` ⇔ `--surface-2`).
@immutable
class OrbitTokens extends ThemeExtension<OrbitTokens> {
  const OrbitTokens({
    required this.bg,
    required this.surface1,
    required this.surface2,
    required this.surface3,
    required this.outline,
    required this.accent,
    required this.onAccent,
    required this.cta,
    required this.onCta,
    required this.success,
    required this.warning,
    required this.danger,
    required this.text,
    required this.textMuted,
    required this.idle,
    required this.shadow,
  });

  static const dark = OrbitTokens(
    bg: Color(0xff191624),
    surface1: Color(0xff241e33),
    surface2: Color(0xff2a2340),
    surface3: Color(0xff2e2742),
    outline: Color(0x2ebc93ed),
    accent: Color(0xffbc93ed),
    onAccent: Color(0xff1e1430),
    cta: Color(0xffff7e88),
    onCta: Color(0xff33101c),
    success: Color(0xff4cd964),
    warning: Color(0xfff4b04d),
    danger: Color(0xffe25b6b),
    text: Color(0xfff5efff),
    textMuted: Color(0xffa89dbf),
    idle: Color(0xff7d7393),
    shadow: Color(0x47000000),
  );

  static const light = OrbitTokens(
    bg: Color(0xfffaf5ff),
    surface1: Color(0xffffffff),
    surface2: Color(0xfff4edfc),
    surface3: Color(0xffeadff6),
    outline: Color(0x2e7753a5),
    accent: Color(0xff7753a5),
    onAccent: Color(0xffffffff),
    cta: Color(0xffa53649),
    onCta: Color(0xffffffff),
    success: Color(0xff1f7a35),
    warning: Color(0xff8a5300),
    danger: Color(0xffba1a1a),
    text: Color(0xff2b2238),
    textMuted: Color(0xff6b5f7e),
    idle: Color(0xff9a90aa),
    shadow: Color(0x1a7753a5),
  );

  // Spacing scale (multiples of 8) and radii shared by both themes.
  static const space1 = 8.0;
  static const space2 = 16.0;
  static const space3 = 24.0;
  static const space4 = 32.0;
  static const radiusS = 12.0;
  static const radiusM = 16.0;
  static const radiusL = 24.0;

  final Color bg, surface1, surface2, surface3, outline, accent, onAccent;
  final Color cta, onCta, success, warning, danger, text, textMuted, idle;
  final Color shadow;

  static OrbitTokens of(BuildContext context) =>
      Theme.of(context).extension<OrbitTokens>() ??
      (Theme.of(context).brightness == Brightness.dark ? dark : light);

  @override
  OrbitTokens copyWith() => this;

  @override
  OrbitTokens lerp(ThemeExtension<OrbitTokens>? other, double t) {
    if (other is! OrbitTokens) return this;
    Color m(Color a, Color b) => Color.lerp(a, b, t)!;
    return OrbitTokens(
      bg: m(bg, other.bg),
      surface1: m(surface1, other.surface1),
      surface2: m(surface2, other.surface2),
      surface3: m(surface3, other.surface3),
      outline: m(outline, other.outline),
      accent: m(accent, other.accent),
      onAccent: m(onAccent, other.onAccent),
      cta: m(cta, other.cta),
      onCta: m(onCta, other.onCta),
      success: m(success, other.success),
      warning: m(warning, other.warning),
      danger: m(danger, other.danger),
      text: m(text, other.text),
      textMuted: m(textMuted, other.textMuted),
      idle: m(idle, other.idle),
      shadow: m(shadow, other.shadow),
    );
  }
}

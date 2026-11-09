import 'package:flutter/material.dart';

import 'ui/motion.dart';
import 'ui/tokens.dart';

export 'ui/tokens.dart' show OrbitTokens;

Color orbitWarning(Brightness brightness) => brightness == Brightness.dark
    ? OrbitTokens.dark.warning
    : OrbitTokens.light.warning;

/// Soft Orbit theme built from explicit tokens (docs/design/soft-orbit-tokens.md).
/// Styling only: it never changes tunnel behaviour or navigation.
ThemeData orbitTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final t = dark ? OrbitTokens.dark : OrbitTokens.light;
  final scheme = ColorScheme(
    brightness: brightness,
    primary: t.accent,
    onPrimary: t.onAccent,
    primaryContainer: t.surface3,
    onPrimaryContainer: t.text,
    secondary: t.cta,
    onSecondary: t.onCta,
    tertiary: t.success,
    onTertiary: t.bg,
    error: t.danger,
    onError: dark ? const Color(0xff2a0a10) : Colors.white,
    surface: t.surface1,
    onSurface: t.text,
    onSurfaceVariant: t.textMuted,
    surfaceContainerLowest: t.bg,
    surfaceContainerLow: t.surface1,
    surfaceContainer: t.surface1,
    surfaceContainerHigh: t.surface2,
    surfaceContainerHighest: t.surface3,
    outline: t.textMuted.withValues(alpha: .5),
    outlineVariant: t.outline,
    shadow: t.shadow,
    inverseSurface: t.text,
    onInverseSurface: t.bg,
    inversePrimary: t.accent,
  );
  final base =
      (dark ? Typography.material2021().white : Typography.material2021().black)
          .apply(bodyColor: t.text, displayColor: t.text);
  final text = base.copyWith(
    displaySmall: base.displaySmall?.copyWith(
      fontSize: 40,
      fontWeight: FontWeight.w700,
      height: 1.1,
      letterSpacing: -.5,
    ),
    headlineMedium: base.headlineMedium?.copyWith(
      fontSize: 28,
      fontWeight: FontWeight.w700,
      height: 1.2,
    ),
    headlineSmall: base.headlineSmall?.copyWith(
      fontSize: 22,
      fontWeight: FontWeight.w600,
      height: 1.25,
    ),
    titleLarge: base.titleLarge?.copyWith(
      fontSize: 20,
      fontWeight: FontWeight.w600,
    ),
    titleMedium: base.titleMedium?.copyWith(
      fontSize: 16,
      fontWeight: FontWeight.w600,
    ),
    bodyLarge: base.bodyLarge?.copyWith(fontSize: 16, height: 1.4),
    bodyMedium: base.bodyMedium?.copyWith(fontSize: 15, height: 1.4),
    bodySmall: base.bodySmall?.copyWith(
      fontSize: 13,
      fontWeight: FontWeight.w500,
      color: t.textMuted,
      height: 1.35,
    ),
    labelLarge: base.labelLarge?.copyWith(
      fontSize: 16,
      fontWeight: FontWeight.w600,
    ),
  );
  const pill = StadiumBorder();
  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    textTheme: text,
    extensions: [t],
    scaffoldBackgroundColor: t.bg,
    canvasColor: t.bg,
    splashFactory: InkSparkle.splashFactory,
    pageTransitionsTheme: PageTransitionsTheme(
      builders: {
        for (final platform in TargetPlatform.values)
          platform: const OrbitPageTransitionsBuilder(),
      },
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      foregroundColor: t.text,
      elevation: 0,
      scrolledUnderElevation: 0,
      titleTextStyle: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: t.surface1,
      surfaceTintColor: Colors.transparent,
      indicatorColor: t.surface3,
      indicatorShape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(OrbitTokens.radiusM),
      ),
      height: 72,
      iconTheme: WidgetStateProperty.resolveWith(
        (s) => IconThemeData(
          color: s.contains(WidgetState.selected) ? t.accent : t.textMuted,
        ),
      ),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (s) => TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: s.contains(WidgetState.selected) ? t.accent : t.textMuted,
        ),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: t.cta,
        foregroundColor: t.onCta,
        disabledBackgroundColor: t.surface3,
        disabledForegroundColor: t.textMuted,
        minimumSize: const Size(48, 56),
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
        shape: pill,
        textStyle: text.labelLarge,
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: t.text,
        minimumSize: const Size(48, 52),
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
        side: BorderSide(color: t.outline, width: 1.5),
        shape: pill,
        textStyle: text.labelLarge,
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: t.accent,
        minimumSize: const Size(48, 48),
        shape: pill,
        textStyle: text.labelLarge?.copyWith(fontSize: 15),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: t.surface2,
      labelStyle: TextStyle(color: t.textMuted),
      hintStyle: TextStyle(color: t.textMuted),
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(OrbitTokens.radiusM),
        borderSide: BorderSide(color: t.outline),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(OrbitTokens.radiusM),
        borderSide: BorderSide(color: t.outline),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(OrbitTokens.radiusM),
        borderSide: BorderSide(color: t.accent, width: 1.5),
      ),
    ),
    cardTheme: CardThemeData(
      color: t.surface1,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(OrbitTokens.radiusL),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: t.surface1,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(OrbitTokens.radiusL),
      ),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: t.surface1,
      surfaceTintColor: Colors.transparent,
      dragHandleColor: t.textMuted,
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: t.accent,
      linearTrackColor: t.surface3,
      circularTrackColor: Colors.transparent,
    ),
    listTileTheme: ListTileThemeData(
      iconColor: t.accent,
      textColor: t.text,
      subtitleTextStyle: text.bodySmall,
    ),
    dividerTheme: DividerThemeData(color: t.outline, space: 1),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: t.surface3,
      contentTextStyle: TextStyle(color: t.text),
    ),
    expansionTileTheme: ExpansionTileThemeData(
      iconColor: t.textMuted,
      collapsedIconColor: t.textMuted,
      shape: const Border(),
      collapsedShape: const Border(),
    ),
    radioTheme: RadioThemeData(
      fillColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected) ? t.accent : t.textMuted,
      ),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected) ? t.onAccent : t.textMuted,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected) ? t.accent : t.surface3,
      ),
    ),
    checkboxTheme: CheckboxThemeData(
      fillColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected) ? t.accent : null,
      ),
      checkColor: WidgetStatePropertyAll(t.onAccent),
    ),
  );
}

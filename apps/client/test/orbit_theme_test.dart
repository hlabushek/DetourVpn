import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/orbit_theme.dart';
import 'package:vpn_client/quota.dart';

void main() {
  test(
    'soft orbit tokens have readable text and distinct error/warning colors',
    () {
      double contrast(Color a, Color b) {
        final x = a.computeLuminance(), y = b.computeLuminance();
        return x > y ? (x + 0.05) / (y + 0.05) : (y + 0.05) / (x + 0.05);
      }

      for (final brightness in Brightness.values) {
        final theme = orbitTheme(brightness);
        final s = theme.colorScheme;
        final t = theme.extension<OrbitTokens>()!;
        expect(contrast(s.surface, s.onSurface), greaterThanOrEqualTo(4.5));
        expect(contrast(t.bg, t.text), greaterThanOrEqualTo(4.5));
        expect(contrast(t.surface1, t.textMuted), greaterThanOrEqualTo(4.5));
        expect(contrast(s.primary, s.onPrimary), greaterThanOrEqualTo(4.5));
        expect(contrast(t.cta, t.onCta), greaterThanOrEqualTo(4.5));
        expect(contrast(s.surface, s.error), greaterThanOrEqualTo(4.5));
        expect(
          contrast(s.surface, orbitWarning(brightness)),
          greaterThanOrEqualTo(4.5),
        );
        expect(s.error, isNot(orbitWarning(brightness)));
      }
    },
  );

  test(
    'reward history preserves exact integer bytes and identifies the source',
    () {
      final entry = QuotaEntry.fromJson({
        'sequence': 2,
        'delta_bytes': 9007199254740993,
        'balance_after_bytes': 9007199254740993,
        'kind': 'credit',
        'source_type': 'reward',
        'created_at': '2026-10-21T12:00:00Z',
      });
      expect(entry.label, 'Бонусный трафик');
      expect(byteText(entry.delta), '9 007 199 254 740 993 байт');
    },
  );
}

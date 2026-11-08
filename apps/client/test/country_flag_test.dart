import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/country_flag.dart';

void main() {
  test('country code parsing is strict and non-fatal', () {
    expect(parseCountryCode('FI'), 'FI');
    for (final bad in ['fi', 'FIN', '', 'F1', null, 7]) {
      expect(parseCountryCode(bad), isNull);
    }
    expect(parseCity('Helsinki'), 'Helsinki');
    expect(parseCity(''), isNull);
    expect(parseCity('x' * 65), isNull);
  });

  testWidgets('known code draws a flag, unknown or missing code a globe', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Column(
          children: [CountryFlag('FI'), CountryFlag('ZZ'), CountryFlag(null)],
        ),
      ),
    );
    expect(find.byIcon(Icons.public), findsNWidgets(2));
    expect(find.byType(CustomPaint), findsWidgets);
    expect(find.bySemanticsLabel('Флаг FI'), findsOneWidget);
  });
}

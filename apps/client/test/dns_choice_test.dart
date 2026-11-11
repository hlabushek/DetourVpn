import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/dns_choice.dart';

void main() {
  test(
    'only the four listed resolvers are stored; everything else is default',
    () {
      expect(dnsChoiceServers, [
        '1.1.1.1',
        '8.8.8.8',
        '94.140.14.14',
        '9.9.9.9',
      ]);
      expect(parseDnsChoice('9.9.9.9'), '9.9.9.9');
      expect(parseDnsChoice(' 1.1.1.1 '), '1.1.1.1');
      expect(parseDnsChoice(''), isNull);
      expect(parseDnsChoice(null), isNull);
      expect(parseDnsChoice('10.0.0.1'), isNull);
      expect(parseDnsChoice('1.1.1.1; drop'), isNull);
    },
  );

  test('wire form is the server or an empty string for the default', () {
    expect(dnsChoiceWire('8.8.8.8'), '8.8.8.8');
    expect(dnsChoiceWire(null), '');
    expect(dnsChoiceWire('203.0.113.7'), '');
  });

  test('labels cover the default and every listed server', () {
    expect(dnsChoiceLabel(null), 'Как в профиле сервера');
    for (final server in dnsChoiceServers) {
      expect(dnsChoiceLabel(server), isNot('Как в профиле сервера'));
      expect(dnsChoiceLabel(server), startsWith(server));
    }
  });
}

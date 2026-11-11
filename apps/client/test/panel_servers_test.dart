import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/panel_servers.dart';

void main() {
  test('panel servers on Android get the Russian not-supported text', () {
    expect(
      serverUnavailableReason(source: 'panel', isAndroid: true),
      'Серверы из панели (VLESS) пока не поддерживаются на Android. '
      'Выберите другой сервер.',
    );
  });

  test('VPS servers and other platforms are not blocked by the client', () {
    expect(serverUnavailableReason(source: 'vps', isAndroid: true), isNull);
    expect(serverUnavailableReason(source: 'panel', isAndroid: false), isNull);
    expect(serverUnavailableReason(source: 'vps', isAndroid: false), isNull);
  });
}

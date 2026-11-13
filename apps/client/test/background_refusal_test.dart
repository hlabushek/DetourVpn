import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';

// ADR-134: a refused background start keeps the intent. The user sees one Russian text that only
// promises what the app does.
void main() {
  test('refused background start has a Russian text that keeps the intent', () {
    final text = messageFor('background_start_refused');
    expect(text, contains('фоне'));
    expect(text, contains('Подключение сохранено'));
    expect(text, contains('«Подключить»'));
    expect(text, isNot(contains('гарантир')));
  });

  test('snapshot stop category is optional and defaults to unknown', () {
    const snapshot = NativeSnapshot(
      up: false,
      network: true,
      handshakeAge: -1,
      remainingMs: 0,
    );
    expect(snapshot.stopCategory, isNull);
    const refused = NativeSnapshot(
      up: false,
      network: true,
      handshakeAge: -1,
      remainingMs: 0,
      stopCategory: 'background_start_refused',
    );
    expect(refused.stopCategory, 'background_start_refused');
  });
}

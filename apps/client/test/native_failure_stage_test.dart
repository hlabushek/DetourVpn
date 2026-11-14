import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/native_tunnel.dart';

void main() {
  test('snapshot failure stage accepts only lowercase stage codes', () {
    expect(nativeFailureStage('native_watchdog_restart'), 'native_watchdog_restart');
    expect(nativeFailureStage('native_renewal'), 'native_renewal');
    // Older native builds send no key; "" means no failure is recorded.
    expect(nativeFailureStage(null), isNull);
    expect(nativeFailureStage(''), isNull);
    expect(nativeFailureStage(42), isNull);
    expect(nativeFailureStage('https://host/path'), isNull);
    expect(nativeFailureStage('Native-Renewal'), isNull);
    expect(nativeFailureStage('x' * 65), isNull);
  });

  test('snapshot without the key has no failure stage', () {
    const snapshot = NativeSnapshot(
      up: false,
      network: true,
      handshakeAge: -1,
      remainingMs: 0,
    );
    expect(snapshot.failureStage, isNull);
  });
}

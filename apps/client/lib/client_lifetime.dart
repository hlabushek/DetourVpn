import 'package:flutter/services.dart';

import 'api.dart';
import 'connection.dart';
import 'native_tunnel.dart';

/// Owned by the application Flutter engine, never by a widget or Activity.
/// One API session and one authorization owner survive UI replacement.
class ClientLifetime {
  ClientLifetime(this.api, this.native, {this.connection}) {
    PlatformTunnel.channel.setMethodCallHandler((call) async {
      if (call.method == 'sessionPulse') {
        await native.onSessionPulse?.call();
      } else if (call.method == 'restoreConnection') {
        final current = connection;
        if (api?.authenticated == true &&
            current != null &&
            !current.hasLease &&
            !current.busy) {
          await current.connect();
        }
      } else {
        throw MissingPluginException();
      }
    });
  }
  final Api? api;
  final PlatformTunnel native;
  ConnectionController? connection;
}

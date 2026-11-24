import 'package:flutter/services.dart';

/// ADR-196 (Windows only): «Блокировать трафик при обрыве». The value lives in the native
/// preferences (the runner reads it at every connect), so Dart only reads and writes it.
/// Default OFF: an unknown, missing or unreadable value is treated as off.
class KillSwitchSetting {
  const KillSwitchSetting([
    this.channel = const MethodChannel('project.vpn/tunnel.v1'),
  ]);
  final MethodChannel channel;

  static const title = 'Блокировать трафик при обрыве';

  Future<bool> read() async {
    try {
      return await channel.invokeMethod<bool>('killSwitchRead') ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

  /// Stores the choice natively and returns the stored value. Turning it off removes an active
  /// block at once; turning it on applies at the next connect. Throws [PlatformException].
  Future<bool> write(bool enabled) async =>
      await channel.invokeMethod<bool>('killSwitchWrite', {
        'enabled': enabled,
      }) ??
      false;
}

String killSwitchSubtitle(bool enabled) => enabled
    ? 'Включено: при обрыве VPN интернет блокируется до восстановления или отключения. Действует со следующего подключения.'
    : 'Выключено: если VPN оборвётся, трафик пойдёт напрямую.';

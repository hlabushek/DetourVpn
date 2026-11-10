import 'dart:async';

import 'package:vpn_client/platform_info.dart';

/// The suite is written for the Android path; tests for the Windows client (I-032) opt in with
/// `debugIsWindowsOverride = true` and reset it, so results do not depend on the host OS.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  debugIsWindowsOverride = false;
  await testMain();
}

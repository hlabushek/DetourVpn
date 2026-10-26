import 'package:flutter/foundation.dart';

import 'api.dart';

/// Compiled in only for an explicitly opted-in debug build.
const bool i026DuplicateOtpProbeEnabled =
    kDebugMode && bool.fromEnvironment('I026_DUPLICATE_OTP_PROBE');

enum DuplicateOtpReplayResult {
  rejected,
  accepted,
  inconclusive,
  cleanupFailed,
}

/// Replays the exact in-memory verify request once. No request contents are logged.
Future<DuplicateOtpReplayResult> probeDuplicateOtpReplay(
  Api api,
  Map<String, dynamic> request,
  Map<String, dynamic> firstSession,
) async {
  late final Map<String, dynamic> replaySession;
  try {
    replaySession = await api.call('POST', '/api/v1/auth/verify', request);
  } on ClientError catch (error) {
    if (error.code == 'unauthorized') return DuplicateOtpReplayResult.rejected;
    return DuplicateOtpReplayResult.inconclusive;
  }

  var cleanupSucceeded = false;
  try {
    await api.acceptSession(replaySession);
    await api.call('POST', '/api/v1/auth/logout');
    cleanupSucceeded = true;
  } catch (_) {
    // The result is reported as inconclusive; never log server or token data.
  } finally {
    await api.forgetSession();
    await api.acceptSession(firstSession);
  }
  return cleanupSucceeded
      ? DuplicateOtpReplayResult.accepted
      : DuplicateOtpReplayResult.cleanupFailed;
}

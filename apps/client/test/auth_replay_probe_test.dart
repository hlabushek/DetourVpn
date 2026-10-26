import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/auth_replay_probe.dart';

// TEST ONLY fake responses. Never used by the debug live probe or release app.
class ProbeApi extends Api {
  ProbeApi(this.responses) : super(Uri.parse('https://example.invalid'));

  final List<Object> responses;
  final List<Map<String, dynamic>?> bodies = [];
  final List<Map<String, dynamic>> accepted = [];
  var forgotten = 0;

  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    bodies.add(body);
    final result = responses.removeAt(0);
    if (result is ClientError) throw result;
    return result as Map<String, dynamic>;
  }

  @override
  Future<void> acceptSession(Map<String, dynamic> value) async {
    accepted.add(value);
  }

  @override
  Future<void> forgetSession() async {
    forgotten++;
  }
}

void main() {
  const request = <String, dynamic>{
    'request_id': 'TEST_ONLY_REQUEST',
    'verifier': 'TEST_ONLY_VERIFIER',
    'code': 'TEST_ONLY_OTP',
  };
  const firstSession = <String, dynamic>{'access_token': 'TEST_ONLY_FIRST'};
  const replaySession = <String, dynamic>{'access_token': 'TEST_ONLY_REPLAY'};

  test('same OTP request is classified as rejected on generic 401', () async {
    final api = ProbeApi([const ClientError('unauthorized')]);

    final result = await probeDuplicateOtpReplay(api, request, firstSession);

    expect(result, DuplicateOtpReplayResult.rejected);
    expect(api.bodies, [request]);
    expect(api.accepted, isEmpty);
    expect(api.forgotten, 0);
  });

  test(
    'unexpected successful replay is logged out and first session restored',
    () async {
      final api = ProbeApi([replaySession, <String, dynamic>{}]);

      final result = await probeDuplicateOtpReplay(api, request, firstSession);

      expect(result, DuplicateOtpReplayResult.accepted);
      expect(api.bodies, [request, isNull]);
      expect(api.accepted, [replaySession, firstSession]);
      expect(api.forgotten, 1);
    },
  );

  test(
    'transport failure is inconclusive and does not replace the session',
    () async {
      final api = ProbeApi([const ClientError('network_unavailable')]);

      final result = await probeDuplicateOtpReplay(api, request, firstSession);

      expect(result, DuplicateOtpReplayResult.inconclusive);
      expect(api.accepted, isEmpty);
      expect(api.forgotten, 0);
    },
  );

  test('failed cleanup is not reported as rejection', () async {
    final api = ProbeApi([
      replaySession,
      const ClientError('network_unavailable'),
    ]);

    final result = await probeDuplicateOtpReplay(api, request, firstSession);

    expect(result, DuplicateOtpReplayResult.cleanupFailed);
    expect(api.accepted, [replaySession, firstSession]);
    expect(api.forgotten, 1);
  });
}

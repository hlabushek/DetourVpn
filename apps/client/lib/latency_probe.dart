import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

/// Measures round-trip time to a node's UDP latency echo (ADR-102). Returns milliseconds, or
/// null when the node did not answer. A measurement, never an estimate: no answer is shown as
/// "нет ответа", not as a guessed number.
abstract interface class LatencyMeter {
  Future<int?> measure(String host, int port);
}

/// Sends [attempts] 16-byte probes (`DVP1` + 12 random bytes) and reports the median reply time.
///
/// On Android the app's own traffic is excluded from the VPN, so this is the phone-to-node path
/// even while connected. Only an IPv4 literal from the API catalog and an unprivileged port are
/// accepted; replies from any other address, port or nonce are ignored.
class UdpLatencyMeter implements LatencyMeter {
  const UdpLatencyMeter({
    this.attempts = 3,
    this.timeout = const Duration(milliseconds: 1500),
  });
  final int attempts;
  final Duration timeout;

  static final _random = Random.secure();
  static const _request = [0x44, 0x56, 0x50, 0x31]; // DVP1
  static const _reply = [0x44, 0x56, 0x52, 0x31]; // DVR1

  @override
  Future<int?> measure(String host, int port) async {
    final address = InternetAddress.tryParse(host);
    if (address == null ||
        address.type != InternetAddressType.IPv4 ||
        port <= 1024 ||
        port > 65535) {
      return null;
    }
    RawDatagramSocket? socket;
    StreamSubscription<RawSocketEvent>? subscription;
    try {
      socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
      Completer<int>? pending;
      List<int>? nonce;
      final watch = Stopwatch();
      subscription = socket.listen((event) {
        if (event != RawSocketEvent.read) return;
        final datagram = socket!.receive();
        final waiting = pending;
        if (datagram == null || waiting == null || waiting.isCompleted) return;
        final data = datagram.data;
        if (datagram.address != address ||
            datagram.port != port ||
            data.length != 16) {
          return;
        }
        for (var i = 0; i < 4; i++) {
          if (data[i] != _reply[i]) return;
        }
        for (var i = 0; i < 12; i++) {
          if (data[4 + i] != nonce![i]) return;
        }
        waiting.complete(max(1, watch.elapsedMilliseconds));
      });
      final samples = <int>[];
      for (var attempt = 0; attempt < attempts; attempt++) {
        nonce = List.generate(12, (_) => _random.nextInt(256));
        pending = Completer<int>();
        watch
          ..reset()
          ..start();
        socket.send(Uint8List.fromList([..._request, ...nonce]), address, port);
        try {
          samples.add(await pending.future.timeout(timeout));
        } on TimeoutException {
          // Lost probe: try the next one.
        }
      }
      if (samples.isEmpty) return null;
      samples.sort();
      return samples[samples.length ~/ 2];
    } on SocketException {
      return null;
    } finally {
      await subscription?.cancel();
      socket?.close();
    }
  }
}

/// Color band for a measured latency: good, fair, or slow/unreachable.
enum LatencyBand { good, fair, poor }

LatencyBand latencyBand(int? ms) => ms == null
    ? LatencyBand.poor
    : ms < 80
    ? LatencyBand.good
    : ms < 200
    ? LatencyBand.fair
    : LatencyBand.poor;

String latencyLabel(int? ms) => ms == null ? 'нет ответа' : '$ms мс';

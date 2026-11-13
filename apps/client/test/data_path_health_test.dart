import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/native_tunnel.dart';

// ADR-133: mirrors DataPathWatchTest.java. Same thresholds, same rules.
void main() {
  const min = 60000;
  const flow = 120000;

  test('unknown counters change nothing', () {
    final w = DataPathHealth();
    expect(w.observe(null, 100, 0), DataPathState.unknown);
    expect(w.observe(10, -1, 1000), DataPathState.unknown);
    expect(w.state(10 * min), DataPathState.unknown);
  });

  test('inbound progress is flowing for the flow window only', () {
    final w = DataPathHealth();
    w.observe(100, 150, 0);
    expect(w.observe(120, 170, 10000), DataPathState.flowing);
    expect(w.state(10000 + flow), DataPathState.flowing);
    expect(w.state(10001 + flow), DataPathState.quiet);
  });

  test('an idle phone is quiet, never stalled', () {
    final w = DataPathHealth();
    w.observe(100, 150, 0);
    expect(w.observe(100, 151, 10 * min), DataPathState.quiet);
  });

  test('outbound without inbound for the window is a stall', () {
    final w = DataPathHealth();
    w.observe(100, 150, 0);
    w.observe(100, 4150, 60000);
    expect(w.state(60000), DataPathState.quiet);
    w.observe(100, 9150, 110000);
    expect(w.state(119999), DataPathState.quiet);
    expect(w.state(flow), DataPathState.stalled);
  });

  test('one inbound byte ends a stall', () {
    final w = DataPathHealth();
    w.observe(100, 150, 0);
    w.observe(100, 20150, 130000);
    expect(w.state(130000), DataPathState.stalled);
    expect(w.observe(101, 40151, 140000), DataPathState.flowing);
  });

  test('never receiving anything is a stall once outbound accumulates', () {
    final w = DataPathHealth();
    w.observe(0, 0, 0);
    expect(w.observe(0, 9000, 5000), DataPathState.quiet);
    expect(w.state(flow + 5000), DataPathState.stalled);
  });

  test('a counter reset starts a fresh baseline', () {
    final w = DataPathHealth();
    w.observe(500, 900, 0);
    w.observe(500, 920000, 10000);
    expect(w.observe(10, 30, 20000), DataPathState.quiet);
    expect(w.state(20000 + flow - 1), DataPathState.quiet);
  });

  test('reset forgets everything', () {
    final w = DataPathHealth();
    w.observe(0, 0, 0);
    w.observe(0, 20000, 1000);
    w.reset();
    expect(w.state(10 * min), DataPathState.unknown);
  });
}

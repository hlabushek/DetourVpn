import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/node_choice.dart';
import 'package:vpn_client/panel_servers.dart' show panelServerSource;

NodeOption node(
  String id, {
  String reason = 'available',
  String source = 'vps',
}) => NodeOption(id, id, 'region', reason, source: source);

void main() {
  group('ADR-199 automatic node choice', () {
    test('no eligible node gives null', () {
      expect(chooseNode([]), isNull);
      expect(
        chooseNode([
          node('a', reason: 'capacity'),
          node('b', reason: 'heartbeat_stale'),
          node('p', source: panelServerSource),
        ]),
        isNull,
      );
    });

    test('lowest measured latency wins', () {
      final nodes = [node('a'), node('b'), node('c')];
      expect(chooseNode(nodes, latencyMs: {'a': 120, 'b': 40, 'c': 80}), 'b');
    });

    test('unavailable and panel nodes are never chosen, even when fastest', () {
      final nodes = [
        node('slow'),
        node('busy', reason: 'capacity'),
        node('panel', source: panelServerSource),
      ];
      expect(
        chooseNode(nodes, latencyMs: {'slow': 300, 'busy': 5, 'panel': 5}),
        'slow',
      );
    });

    test('a node with no echo answer ranks below an unmeasured one', () {
      final nodes = [node('silent'), node('unknown')];
      expect(chooseNode(nodes, latencyMs: {'silent': null}), 'unknown');
      expect(
        nodeChoiceScore(nodes[0], {'silent': null}, const {}),
        nodeChoiceNoAnswerMs,
      );
      expect(
        nodeChoiceScore(nodes[1], const {}, const {}),
        nodeChoiceUnmeasuredMs,
      );
    });

    test('recent failures are a penalty, capped', () {
      final nodes = [node('a'), node('b')];
      // a is faster but failed twice: 50 + 300 > 200.
      expect(
        chooseNode(
          nodes,
          latencyMs: {'a': 50, 'b': 200},
          recentFailures: {'a': 2},
        ),
        'b',
      );
      expect(
        nodeChoiceScore(nodes[0], {'a': 50}, {'a': 99}),
        50 + nodeChoiceMaxCountedFailures * nodeChoiceFailurePenaltyMs,
      );
      expect(nodeChoiceScore(nodes[0], {'a': 50}, {'a': -3}), 50);
    });

    test(
      'excluded nodes are skipped (failover away from the failing node)',
      () {
        final nodes = [node('a'), node('b')];
        expect(
          chooseNode(nodes, latencyMs: {'a': 10, 'b': 90}, exclude: {'a'}),
          'b',
        );
        expect(chooseNode([node('a')], exclude: {'a'}), isNull);
      },
    );

    test('the current node is kept within the hysteresis margin', () {
      final nodes = [node('a'), node('b')];
      // 100 vs 75: gain 25 ms is not more than 30 ms.
      expect(
        chooseNode(nodes, latencyMs: {'a': 100, 'b': 75}, current: 'a'),
        'a',
      );
      // 400 vs 360: gain 40 ms but only 10 %.
      expect(
        chooseNode(nodes, latencyMs: {'a': 400, 'b': 360}, current: 'a'),
        'a',
      );
      // 100 vs 60: gain 40 ms and 40 %: switch.
      expect(
        chooseNode(nodes, latencyMs: {'a': 100, 'b': 60}, current: 'a'),
        'b',
      );
    });

    test(
      'a current node that is no longer eligible or excluded is not kept',
      () {
        final nodes = [node('a', reason: 'disabled'), node('b')];
        expect(
          chooseNode(nodes, latencyMs: {'a': 1, 'b': 500}, current: 'a'),
          'b',
        );
        expect(
          chooseNode(
            [node('a'), node('b')],
            latencyMs: {'a': 10, 'b': 30},
            current: 'a',
            exclude: {'a'},
          ),
          'b',
        );
      },
    );

    test('ties keep catalog order', () {
      final nodes = [node('first'), node('second')];
      expect(
        chooseNode(nodes, latencyMs: {'first': 50, 'second': 50}),
        'first',
      );
      expect(chooseNode(nodes), 'first');
    });

    test('a negative latency is treated as no answer', () {
      final nodes = [node('bad'), node('ok')];
      expect(chooseNode(nodes, latencyMs: {'bad': -1, 'ok': 900}), 'ok');
    });
  });
}

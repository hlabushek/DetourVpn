import 'connection.dart';

/// ADR-199: automatic node choice by measured quality. Pure (no I/O, no clock, no Flutter), so
/// every rule is unit-tested. Not wired into the app yet: callers keep the manual choice and the
/// ADR-148 `failoverNode` rule until the owner decides where automatic choice is used.
///
/// Inputs are only what the client already has: the catalog ([NodeOption.reason], source), the
/// ADR-102 latency map (`null` = no answer, absent = not measured) and a local count of recent
/// failed connects per node. Nothing here is sent anywhere.

/// Penalty added to a node's latency per recent failed connect.
const nodeChoiceFailurePenaltyMs = 150;

/// At most this many failures are counted (so one bad evening cannot bury a node forever).
const nodeChoiceMaxCountedFailures = 4;

/// Score used for a node that is available but not measured (no echo advertised, or not yet).
const nodeChoiceUnmeasuredMs = 400;

/// Score used for a node whose latency echo did not answer.
const nodeChoiceNoAnswerMs = 2000;

/// Hysteresis: the current node is kept unless another one is better by more than both bounds.
const nodeChoiceKeepMarginMs = 30;
const nodeChoiceKeepMarginPercent = 20;

/// Whether [node] may be chosen automatically: available now and an AmneziaWG (`vps`) node.
/// Panel (VLESS) nodes are left to a manual choice, as in ADR-148's failover rule.
bool nodeAutoEligible(NodeOption node) =>
    node.reason == 'available' && !node.isPanel;

/// The comparable score of [node] in milliseconds (lower is better).
int nodeChoiceScore(
  NodeOption node,
  Map<String, int?> latencyMs,
  Map<String, int> recentFailures,
) {
  final int base;
  if (!latencyMs.containsKey(node.id)) {
    base = nodeChoiceUnmeasuredMs;
  } else {
    final measured = latencyMs[node.id];
    base = measured == null || measured < 0 ? nodeChoiceNoAnswerMs : measured;
  }
  final failures = (recentFailures[node.id] ?? 0).clamp(
    0,
    nodeChoiceMaxCountedFailures,
  );
  return base + failures * nodeChoiceFailurePenaltyMs;
}

/// The node to use, or null when no node is eligible.
///
/// - Only [nodeAutoEligible] nodes, minus [exclude] (for example the node that just failed).
/// - Lowest [nodeChoiceScore] wins; ties keep catalog order.
/// - [current], if still eligible and not excluded, is kept unless the best node beats it by
///   more than [nodeChoiceKeepMarginMs] **and** [nodeChoiceKeepMarginPercent] percent, so small
///   latency noise never switches nodes.
String? chooseNode(
  List<NodeOption> nodes, {
  Map<String, int?> latencyMs = const {},
  Map<String, int> recentFailures = const {},
  String? current,
  Set<String> exclude = const {},
}) {
  NodeOption? best;
  var bestScore = 0;
  NodeOption? kept;
  var keptScore = 0;
  for (final node in nodes) {
    if (!nodeAutoEligible(node) || exclude.contains(node.id)) continue;
    final score = nodeChoiceScore(node, latencyMs, recentFailures);
    if (best == null || score < bestScore) {
      best = node;
      bestScore = score;
    }
    if (node.id == current) {
      kept = node;
      keptScore = score;
    }
  }
  if (best == null) return null;
  if (kept != null && kept.id != best.id) {
    final gain = keptScore - bestScore;
    final enough =
        gain > nodeChoiceKeepMarginMs &&
        gain * 100 > keptScore * nodeChoiceKeepMarginPercent;
    if (!enough) return kept.id;
  }
  return best.id;
}

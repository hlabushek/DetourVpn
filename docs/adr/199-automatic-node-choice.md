# ADR-199 — Automatic node choice by measured quality (client, pure rule)

**Status:** proposed 2026-11-27 (overnight run, step B1). Pure Dart rule and unit tests only.
**Not wired into the app**: no behaviour change, no server change, no deploy.
**Builds on:** ADR-031/092 (node availability), ADR-102 (UDP latency echo), ADR-148 (failover
`failoverNode`), ADR-115 (panel nodes).

## Problem

The user picks a node by hand. The client already measures latency to each node (ADR-102) and
knows which nodes the catalog reports `available`, but nothing uses these to pick a node. The
ADR-148 failover takes "the first other available node", which can be the slowest one.

## Decision (this step)

New pure module `apps/client/lib/node_choice.dart`:

- **Eligible:** `reason == 'available'` and not a panel (VLESS) node, minus an `exclude` set
  (for example the node that just failed). Same availability rule as a manual choice.
- **Score (ms, lower is better):** measured latency; an unmeasured node counts as 400 ms; a node
  whose echo did not answer (or reported a negative value) counts as 2000 ms. Each recent failed
  connect on that node adds 150 ms, at most 4 counted.
- **Hysteresis:** the current node is kept unless the best node is better by more than 30 ms
  **and** more than 20 %. This stops small latency noise from switching nodes.
- **Ties:** catalog order.
- Returns `null` when no node is eligible; the caller keeps its current behaviour.

Inputs are only data the client already holds. Nothing new is measured, stored or sent.

## Not decided here (owner)

Where automatic choice is used, each a separate small step with its own test:

1. as an «Авто» option in the region picker (user opt-in, saved choice unchanged);
2. inside ADR-148 failover instead of "first other available node" (failover itself is off by
   default);
3. for the first connect when no node was ever saved.

Also open: where the "recent failures" count comes from (an in-memory count of failed connects
per node in the controller is the smallest option) and how long it lasts.

## Security and privacy

- No new network traffic, permission or server endpoint. Latency results stay on the device, as
  today.
- A node can never be chosen when the catalog does not report it available, so the server keeps
  control of which nodes accept connections.
- The constants are design choices, not measurements.

## Verification

- `flutter test test/node_choice_test.dart`: 10 passed (empty and ineligible lists, latency order,
  panel and unavailable never chosen, no-answer vs unmeasured, failure penalty and cap, exclude,
  hysteresis both ways, ineligible current node, ties, negative latency).
- Full `flutter test` and `flutter analyze` (ASCII copy) results are in the morning report.
- Not verified on a device; not used by any screen yet.

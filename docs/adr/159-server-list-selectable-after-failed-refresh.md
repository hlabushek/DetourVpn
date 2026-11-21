# ADR-159 — The last loaded server list stays selectable after a failed refresh

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Unit-test
verified only; not verified on a device.**
**Builds on:** ADR-092 (manual server choice), ADR-145 (2.3, saved node kept when the list fails).

## Problem
`RegionPickerScreen.open` starts a catalog refresh. When that refresh failed (no network for a
moment, API timeout), `nodesError` was set and the picker disabled every server card
(`enabled = !locked && c.nodesError == null && ...`), although both controllers keep the last
successfully parsed `nodes` list on failure. The user had to pull to refresh until a request
succeeded before any server could be chosen.

## Decision
- A card's `enabled` no longer depends on `nodesError`: a node that the last successful catalog
  reported as `available` can be selected while the error is shown.
- The error stays visible as a non-blocking notice (`nodes-stale-notice`): «Список не обновился:
  показан последний загруженный, статусы могли устареть.»; with no list at all: «Не удалось
  загрузить список серверов. Потяните вниз, чтобы повторить.»
- Unchanged: `selectNode` still accepts only a node listed as `available`; a held lease or a busy
  controller still locks the list; the headless restore engine still refuses to start with a
  failed catalog (`node_validation_failed`, ADR-121).

## Assumptions
- Choosing a node with a stale status is safe: the server validates the node at lease creation
  (`tunnel_node_unavailable` / `window_node_unavailable`), which the existing error paths show.

## Security and privacy
No change to what is sent; only the client-side enablement of a UI choice. The server remains the
authority on node availability.

## Test plan and results
- `apps/client/test/node_failover_test.dart` "a failed catalog refresh keeps the last list
  selectable": a successful refresh, then failing refreshes (including the picker's own); the
  notice is shown and tapping a server selects it.
- `flutter test test/node_failover_test.dart test/latency_test.dart`: 15 passed. Full run in HANDOVER.

## Not verified
No device run.

# Beta.22: hung first LTE connection and egress verification — analysis (2026-11-08)

Status: analysis of already collected data plus a client-side mitigation. No new device or
server run was made. Nothing here proves a root cause.

## What the data says

Sources: ADR-089, `docs/evidence/i-030-beta22-unknown-diagnostic-20261108.json` (untracked
at the time of writing), and the I-030 append note (ADR-088 attempts). The hung-connection
receipt `i-030-beta22-lte-success-20261108.json` is not in this checkout.

- Attempt 1 (LTE): first connection sent a 148 B request and got a 116 B response, with no
  completed handshake in about 20 s; after the owner reconnected the tunnel worked.
- The hung generation left one `unknown` receipt (generation 2, slot 1, 16 MiB), accepted
  2 s after its hold, in the same node poll as generation 1's `measured` receipt (789 B).
  Same pattern as ADR-082.
- Later holds on the same connection and attempts 2 (Wi-Fi) and 3 (LTE) were `measured`.
  1 of 3 attempts hung, so the rate is unknown.

## What is not established

- Why no handshake completed: not known. The sizes alone do not separate a lost
  handshake-initiation, a lost response, a carrier NAT/MTU issue, or a node-side slot race.
- Whether the `unknown` receipt is a consequence of the hang or an independent accounting race
  at hold roll-over. The timing (receipt 2 s after the hold, together with generation 1) fits an
  accounting race at least as well as a data-plane fault; both can be true.

## Mitigation added (client, advisory)

`WindowConnectionController` now shows "Handshake не получен более 25 с. Нажмите «Отключить»
и подключитесь заново" when no handshake was confirmed 25 s after the native tunnel started.
There is no automatic reconnect: each new window reserves 16 MiB, and the unknown-window
mechanism requires operator reconciliation (`canRecover=false`). A tester who sees the hint
should reconnect once and report the stage code via the diagnostic share flow.

## Data to collect next (read-only; needs the owner's approval per run)

1. For any hang: node-side handshake/counter samples for the slot during the window
   (`scripts/i030_node_readonly_monitor.py`, counters only, no capture).
2. Client stage code from the diagnostic share flow (`lease_create`, `native_tunnel_start`, …).
3. Whether the hang correlates with a first connection after the phone changed network, or with
   a cold radio state (LTE idle to connected). A scripted pattern of cold-start LTE connects
   would answer this, but ADR-088 caps the series at three attempts; more needs a new ADR.

## Independent egress verification (protocol, not run)

Owner-reported egress is not evidence. Minimum independent check, per tested network:
1. On the phone with the VPN up, request the exit address from an external echo service that
   the owner chooses and records its name; save only the returned address and its ASN/country.
2. Compare with the beta node's public IPv4 recorded in the node receipt.
3. In the same window, compare the node's read-only byte counters with the app's quota delta.
4. Record as a sanitized receipt in `docs/evidence/` (no tester identity, no destination list).
This proves the egress address for that run only; it says nothing about DNS or IPv6 privacy
(ADR-072 defers the broad audit).

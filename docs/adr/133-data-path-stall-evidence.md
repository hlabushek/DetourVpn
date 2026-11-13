# ADR-133 — Data-path stall evidence: outbound without inbound is degraded

**Status:** accepted 2026-11-13 (connection reliability run 3, item A; amends ADR-128).

## Context
ADR-128 derives the health label from handshakes only. A node that answers handshakes but drops
data keeps the label "connected". The Dart UI also treated a fresh handshake as confirmation, so it
hid a dead return path. The native layer already exposes advisory byte counters (`trafficBytes`,
`receivedBytes`, capability `traffic-stats-advisory.v1`), so no native change is needed for the signal.

## Options considered
- Probe the server or the node with an end-to-end request: needs a new endpoint or a request through
  the tunnel. Not in scope for this run (a server-side egress check is a separate issue).
- Keep the handshake age only: the current state; rejected by the acceptance criterion.
- **Use the byte counters**: outbound bytes growing while inbound bytes do not, for a bounded time.
  Chosen. No network call, no new data.

## Decision
- `DataPathWatch` (native) and `DataPathHealth` (Dart) implement the same rule with the same
  thresholds: inbound progress within 120 s means `flowing`; outbound growth of at least 8192 bytes
  with no inbound progress for 120 s means `stalled`; an idle phone is `quiet`, never stalled;
  unknown or reset counters start a fresh baseline.
- Native: a stall makes `transport` report `degraded` even with a fresh handshake
  (`ConnectionSupervisor.transport`, 7-argument form). `evidence` uses `flowing` instead of the
  former hard-coded `false`.
- Dart: a stall makes the status "not confirmed" and the detail text says inbound traffic is missing.
  It does not throw, so the auto-reconnect path (which reacts only to thrown error codes) does not run.
- **A stall never restarts or reconnects.** `TunnelWatchdog` and `ConnectionSupervisor.evaluate` take
  no input from the data path. Tests prove this (`stalledDataPathIsDegradedAndNeverRequestsRecovery`,
  and `ADR-133: a fresh handshake does not hide a stalled data path, and nothing reconnects`).

## Security considerations
- No new data is read or logged. The counters are totals already in the snapshot; no destination,
  DNS name or payload is involved.
- Keepalives (about 32 bytes every 25 s) and handshakes stay far below the 8 KiB threshold, so a quiet
  idle tunnel is not reported as stalled.

## Limits / not verified
- The counters are advisory WireGuard totals. They show that bytes move, not that a site is reachable.
- Not proven on a phone: a real dead node, a Wi-Fi to mobile hand-over, and the 120 s timing under
  Doze. See device test F in `RELIABILITY2_DEVICE_TESTS.md`.
- The label is a heuristic. It does not claim end-to-end reachability (ADR-128).

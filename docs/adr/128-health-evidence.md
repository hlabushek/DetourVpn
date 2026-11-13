# ADR-128 — Health evidence: what the engine can prove, labelled honestly

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2). **Amends:** ADR-099, ADR-122.

## Signals that exist (investigated)
| Signal | Source | Strength |
|---|---|---|
| Core running | `GoBackend.getState` / handle ≥ 0 | proves only that a device exists |
| Peer handshake time | `awgGetConfig` → `last_handshake_time_sec` | the server peer answered (authenticated) — on *some* path |
| Handshake newer than the last physical-network change | handshake age vs `networkChangeAgeMs` | the peer answered on the **current** path |
| Inbound bytes growing | `rx_bytes` | data came back through the tunnel recently |
| Physical network usable / validated | `ConnectivityManager` default-network callbacks | the carrier network exists; says nothing about the VPN |

There is no synthetic probe. The previous DNS probe toward the VPN network is **not** brought
back: the app's own UID is excluded from the VPN in "all"/"exclude" mode and not included in
"include" mode, so any probe from the app process would bypass the tunnel and prove nothing.

## Decision
1. The snapshot reports `evidence` with the strongest true label:
   `none` (down) < `core_running` < `inbound_data` < `peer_handshake` < `peer_and_inbound_data`.
   End-to-end reachability of arbitrary destinations is **not** measurable by the engine and is
   never claimed.
2. Persistent keepalive is 25 s in every profile the API issues (`window_profiles.py:55`);
   wireguard-go rekeys before 120 s when sending (keepalives count), so on a working path the
   handshake is never older than about 2–3 minutes even with zero user traffic. An idle tunnel
   is therefore **not** a failure; only "no handshake for 180 s while a usable network exists"
   (ADR-099) is a stall. No inactivity-only failure exists.
3. `transport` = `degraded` (not `failed`) when the core runs without a fresh handshake; the
   watchdog restart (now real, ADR-127) is the only reaction, with the ADR-099 backoff (2 → 10
   min) so a dead server does not cause a restart storm.
4. Stale results: every watchdog run reads state fresh on the worker thread; a restart scheduled
   for an older network is harmless (it re-reads the current network first).

## Limits
- A server that answers handshakes but drops data (e.g. node egress broken) is reported as
  `peer_handshake` while user traffic fails. Detecting that needs a server-side or routed probe;
  not added.

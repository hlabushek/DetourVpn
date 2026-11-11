# ADR-102 — Measured node latency (ping) in the server list

**Status:** proposed 2026-11-11 (issue B of the owner-approved plan).
**Amends:** ADR-092 ("no latency is shown") and TECHNICAL_SPEC §8 ("latency only if actually
measured"): latency is now shown, and only as a real measurement.

## Decision

- **Node.** `latency_probe.py` runs as its own systemd unit (`detourvpn-beta-latency-probe`):
  - UDP port 51899;
  - root only to read the root-owned install tree, with every capability dropped, a strict
    systemd sandbox and a 64 MiB memory limit.
- **Protocol.**
  - The request is 16 bytes: `DVP1` plus 12 random bytes.
  - The reply is `DVR1` plus the same 12 bytes. It is never larger than the request, so there is
    no amplification.
  - Anything else is dropped.
  - Rate limits: 10 replies/s per source address (burst 20), 2000/s in total, and a bounded
    4096-entry source table.
  - Nothing is logged per request.
- **Catalog.** A manifest node with `latency_probe: true` gets `probe_host` (the slot-1 endpoint's
  IPv4 address) and `probe_port` in `GET /window-tunnels/nodes`. The flag is display-only and not
  part of the manifest digest, so turning it on doesn't break a live node's epoch. The connect
  flow sets it when the node has the probe unit, and the installer installs and starts the unit.
- **Client.** `UdpLatencyMeter` (Dart) sends 3 probes with a 1.5 s timeout each and reports the
  median.
  - It only probes an IPv4 literal from the catalog on an unprivileged port, and ignores replies
    with the wrong address, port or nonce.
  - On Android the app's own traffic is excluded from the VPN, so it measures the phone-to-node
    path even while connected.
  - Display: "NN мс" with a green/yellow/red dot (under 80 ms, under 200 ms, otherwise), or
    "нет ответа".
  - Refresh: a button and pull-to-refresh in the server list, measurement when the list opens,
    and a refresh icon next to the selected server on the home screen.

## Security and privacy
- The echo reveals only that the port is reachable on an already public node address.
- The client sends nothing identifying.
- The control plane never sees measurements.

## Limits / not verified
- Not run on a node or a device. The node's provider firewall must allow UDP 51899 inbound.
- On Windows the client's own traffic may go through the tunnel while connected, so the number
  then includes the tunnel path.

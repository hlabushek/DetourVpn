# ADR-101 — Pool nodes: many simultaneous devices on one node

**Status:** proposed 2026-11-11 (issue A of the owner-approved plan; owner chose "many peers per
slot" over "more slots").
**Amends:** ADR-038 (one peer per slot, per-slot outer-UDP meter), ADR-024/031 for v3 nodes.
**Builds on:** ADR-039 (admission and fencing), ADR-094 (add-node flow), ADR-096 to ADR-100.

## Context

Each v3 slot is an isolated namespace and AWG interface with exactly one peer and the fixed client
address `10.203.71.2`. Its gate and byte meter are an nftables timeout and quota on the slot's
outer UDP port. Both beta nodes were installed with one slot, so a second device got
`409 window_node_capacity`. Adding slots costs a namespace, a userspace AWG process and a UDP port
each, and stops at 16.

## Decision

A node can be installed in **pool mode**: `install_beta_node.py --pool-peers N` (2–250, one slot).

- **Data plane.** Slot 1 keeps its namespace, interface `dv3s1`, port 51901, veth and host DNAT,
  all unchanged. Only these differ:
  - the interface address is `10.203.72.1/24`, and the namespace forward and NAT rules cover
    `10.203.72.0/24`;
  - peer index `n` owns `10.203.72.(n+1)/32`.
- **Peers** (`window_pool.PoolPeer`). A peer is installed with `allowed-ips <address>/32`.
  WireGuard cryptokey routing binds that address to exactly one key, so the inner source address
  is authenticated. The node refuses an address held by another key, and a key already bound to
  another address.
- **Gate and meter** (`window_pool.PoolGate`). Each peer has its own nftables table
  `detour_pool_p<n>` on the forward hook, with:
  - a live set holding the deadline timeout;
  - a byte quota and a counter;
  - accept and drop rules that match only that peer's address, in both directions.

  Every base chain must accept a packet, so one table constrains only its own peer. Replacing a
  table resets one peer and nobody else. Receipts report `meter = "kernel-inner-ip.v1"`: tunnel
  payload bytes, which excludes the roughly 4–10 % of WireGuard and outer overhead that the slot
  meter counted.
- **Agent.** `agent.json` carries `pool_peers`. The daemon builds N gate/peer pairs on one shared
  `PoolInterface`, which re-validates binary and core identity every 10 s and reads nftables
  liveness once per 0.5 s for all peers. It reports `POOL_CAPABILITIES`.
- **Journal.** New journals accept slot ids up to 250. A pool runtime refuses a journal created
  with the old `1..16` schema (`journal_slot_range_legacy`) instead of failing in the middle of an
  admission.
- **Control plane.**
  - `WindowNode.pool_peers` expands the node into N virtual slots (`peer_slots`). Admission still
    takes the lowest free slot under the existing node lock, so it is unchanged.
  - Each virtual slot's client config uses its own `/32` address.
  - `start_epoch` refuses a runtime whose capabilities don't match the manifest mode.
  - Contracts and the DB check allow slot ids up to 250 (migration `0025`, whose downgrade refuses
    while ids above 16 exist).
  - Slot-mode manifests and their digests are byte-for-byte unchanged.

## Security considerations
- Peer isolation: the namespace forward chain only allows `dv3s1 → outer0` for the pool subnet,
  so there is no client-to-client forwarding.
- A wrong or stale table can only affect its own peer index. Startup closes every peer gate
  before reconciliation, as for slots.
- The inner meter undercounts the outer bytes the slot meter used to bill. This is deliberate and
  documented. It never overcounts.

## Limits / not verified
- **Not run on a node.** The nftables program, the forward-hook behaviour and `awg allowed-ips`
  parsing are covered by unit tests with a fake backend only. Run
  `scripts/check_beta_slot_bootstrap.py` and a two-device test on a node first.
- Each peer adds one base chain on the forward hook. That is fine for tens of peers; for hundreds,
  replace it with one table and per-address verdict maps (measure first).
- **In-place conversion** (`scripts/convert_beta_node_pool.py`).
  - The node keeps its id, keys, endpoint and credential.
  - `start_epoch` accepts a changed manifest only when the node is fully drained, using the same
    `runtime_ready` check (no live connection and no window without a receipt). Otherwise
    `window_operator_manifest_changed` still fences it.
  - The tool drains, disables the row, stops the agent, rebuilds slot 1 as a pool with a fresh
    journal, sets `pool_peers` in the manifest, re-enables the row and restarts the agent. Any
    failure restores the node and the manifest.
- Pool mode never installs the per-port slot gate, and deletes a leftover one: a closed port gate
  would drop every pool packet.

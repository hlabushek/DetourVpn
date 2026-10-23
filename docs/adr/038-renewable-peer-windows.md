# ADR-038 — renewable peer windows and isolated slots (I-024)

2026-10-23. Local protocol work in progress; no remote node rollout acceptance.

The v2 node endpoint-wide quota cannot account independently for multiple peers.
Do not widen its 60s lease or remove the subscription compatibility gate.

## Selected design

One fixed operator slot has one network namespace, native AWG interface/core,
UDP endpoint, server key and peer at a time. A node may advertise 1–16 slots.
An address/routing/firewall boundary per slot prevents peer-to-peer access and
ensures that an outer UDP budget belongs to exactly one peer. Capacity must be
measured; sixteen is a protocol bound, not a claim about the 1 GiB server.
Keys remain node-local/client-local. Neither agent input nor user input chooses
commands, executable paths, namespaces, endpoints or firewall programs.

Technical access is a sequence of windows of at most 90s and 64 MiB. Reserve
the next window before dispatch; renew before the previous deadline with a
5s operation margin. The grant deadline is further bounded by funding expiry,
subscription/device/user status and operator runtime. Long subscription periods
do not create long kernel leases. A late renewal cannot reopen an expired peer;
it requires a fresh admission. Replayed windows must match the complete original
grant; changing budget/deadline under the same ID is a conflict. Runtime changes,
revocation and generation fencing invalidate old commands.

Per-window billing uses the dedicated slot's kernel outer IP byte counter,
including transport headers/handshake/obfuscation traffic in both directions.
Do not add AWG native transfer counters to it: that would double-count. The UI
must disclose this metering policy before ordinary use. Counter behavior,
concurrent packet boundary and budget overrun tolerance require real Linux tests.
Existing v2 native-meter evidence does not validate this new meter.

Rotation closes the slot gate, records its final kernel counter durably, then
atomically installs the next budget/deadline and reopens the existing peer.
The tunnel keys/address remain stable. This can briefly pause packets; seamless
handover is not claimed. A final measured receipt is valid only after the old
gate is closed. ACK loss retries the same receipt and reserved window, never a
fresh budget. Local durable records contain IDs/counters/state, not payloads.

## Funding and failure policy

Reserve concrete funding lots in earliest-expiry order, then nonexpiring credit.
Other debits cannot consume those holds. Expiring/revoked held funding must not
be counted as both unavailable and reserved. Final measured settlement can
consume its original held lot after expiry without taking another active lot;
ledger receipts/reconciliation must explicitly represent that authority.

Unknown final counters are **not measured usage** and do not create a guessed
usage debit. Preserve the bounded reservation in an unresolved state for reviewed
operator reconciliation/compensation, with audit and idempotency. A crashed
window cannot renew indefinitely: kernel time/budget expire independently; old
unresolved holds continue to reduce spendable quota. This can temporarily lock
quota and needs operational support. Do not silently release an uncertain hold.

Expiry/revoke is durable intent followed by confirmed gate closure; no premature
slot reuse. Restart changes the node runtime epoch, starts gates closed and
reconciles durable receipts before admission. Independent kernel deadlines must
still close access if the agent/API disappears. Root compromise is outside the
meter trust model; the agent must not be an arbitrary remote execution endpoint.

## Acceptance and rollout gates

First implement strict contracts and their negatives; then DB holds/receipts and
concurrency/restore; then the fixed-operation Linux slot adapter and crash tests;
then Android renewal/lifecycle and physical multiuser/expiry/revoke drills.
The contract module is not mounted by the API before those boundaries exist.
No mocks count as VPN egress, measured kernel enforcement or uninterrupted use.
Permanent node/firewall rollout needs a separate baseline/approval under I-028.

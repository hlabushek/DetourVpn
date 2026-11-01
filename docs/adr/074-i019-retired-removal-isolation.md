# ADR-074: isolate replayed removal of a retired staging lease

Status: implemented locally; physical I-019 verification pending.

## Context

The bounded I-019 node agent uses one kernel UDP gate and one native peer per
node. A device can reuse its public key for a later lease. The lifecycle API
can redeliver an unacknowledged removal for an earlier peer. Previously the
agent closed the shared gate and removed by public key before recognizing that
its durable journal already marked the earlier peer removed. A replay arriving
after a different lease became active could therefore cut off the new lease,
including when both leases use the same device key. Existing physical receipts
do not retain sufficient command ordering to prove this caused their failures.
The next test-only receipt records at most 32 fixed delivery/ACK events with
per-run peer ordinals and whether the ACK handler returned normally; this does
not claim the surrounding database transaction committed. It never records peer
IDs, keys or command payloads. This can test the ordering hypothesis without
expanding production telemetry.

## Decision

For a removal whose own journal record is already `removed`, first validate
the key and version. If exactly one different journal record is active, its
deadline is still in the future, and the native inventory exactly matches
that active record's key, acknowledge the old removal with its preserved final
counters without changing the gate or native peer. Persist a later removal
version if supplied. If the active record or inventory is not uniquely
verifiable, retain the existing fail-closed cleanup behavior. Conflicting keys
or stale versions remain errors; no gate budget or deadline is extended.

This is journal/command integration around the existing pinned AmneziaWG and
kernel nftables components, not a new cryptographic implementation or protocol.
The design assumes the node's existing single-peer lease contract and the
kernel gate's independent deadline and byte limits.

## Verification and limits

Fake-native regression tests cover replay after a newer lease with reused and
distinct keys, stale version rejection, and fail-closed behavior for expired
or inconsistent newer state. They do not prove real VPN behavior. A new bounded
I-019 drill must verify real B handshake, egress, late-command isolation,
ledger settlement and cleanup. `-Prepare` installs the reviewed local agent on
both approved staging hosts; no production rollout is authorized by this ADR.

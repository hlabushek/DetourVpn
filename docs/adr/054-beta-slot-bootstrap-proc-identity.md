# ADR-054 — process identity checks in the beta slot bootstrap unit

Status: accepted for the separately authorized beta node only.

## Context

The fixed slot bootstrap verifies the root UID, network-namespace inode and
pinned executable hash of each systemd-managed AWG core before it writes a
readiness marker. Ubuntu 24.04's service capability bounding prevented the
bootstrap unit from reading `/proc/<pid>/ns/net` and `/proc/<pid>/exe` for the
separate core unit. Treating those `PermissionError`s as success would remove
the identity boundary; retaining the failure made the target unit time out and
roll back every slot.

## Decision

Add `CAP_SYS_PTRACE` to the one-shot `detourvpn-beta-slot-bootstrap.service`
alongside its existing `CAP_SYS_ADMIN`, `CAP_NET_ADMIN` and `CAP_KILL` bounds.
Keep `NoNewPrivileges`, fixed root-owned executable/config paths, closed device
policy, and restricted address families. The capability is used to inspect the
systemd core process; readiness still requires exact root UID, namespace inode
and pinned binary hash. The helper accepts only the observed short-lived
`/usr/lib/systemd/systemd-executor` process labeled `(ip)` while the fixed
`ip netns exec` command transitions to the AWG core. All other identity
mismatches fail closed; timeout or mismatch rolls back only created resources.
During explicit teardown only, the bootstrap may finish cleanup without the
core PID after systemd confirms that the exact core unit is inactive and the
private readiness marker still matches the named namespace inode. An unknown
unit or systemd state remains an error; this exception cannot create readiness
or leave a slot available for traffic. Preserve the root-private `/run` runtime
directory after unit failure so a failed stop retains its readiness marker for
safe retry. Teardown removes the fixed host-side `dvbhN` veth after deleting its
namespace peer, before removing the beta host nft tables.

## Evidence and limits

A TEST ONLY transient unit on the target Ubuntu host reproduced denied `/proc`
reads with the original capability set and successful UID/namespace/hash reads
after adding only `CAP_SYS_PTRACE`. No network request or gate opening occurred
in that probe. The permanent unit must still pass target start/stop and reboot
acceptance; this ADR does not imply node-agent registration or VPN readiness.

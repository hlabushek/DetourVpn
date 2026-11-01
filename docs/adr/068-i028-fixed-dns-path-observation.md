# ADR-068: fixed DNS path observation in I-028 staging

Status: prior test-only staging attempt was inconclusive and cleaned up; revised
local implementation under verification, not physically accepted.

## Context

I-028 receipts show intermittent failures of hostname-based probes and of one
fixed UDP DNS probe. Later TCP/TLS and resolver observations do not establish
what happened to the original DNS packet. Slot-wide AWG counters cannot locate
a drop. Existing ADR-064 observes the physical client network and does not
provide the two necessary node-side points.

## Decision and assumptions

Use the installed upstream `tcpdump`, with a new fixed, test-only `dns-observe`
operator verb, never an arbitrary command or remote capture interface. The
operator requires one live I-028 slot, at least 40 seconds of staging window,
and an exclusive lock. It observes only that slot's tunnel interface and its
namespace uplink. The Android test binds UDP source port 42428 for a single
fixed example.com A query to 1.1.1.1:53. The BPF filter limits source and
destination to that fixed pair and port. The filter deliberately does not
inspect or emit the DNS question or answer. Port collisions, NAT port rewrite,
unrelated matching traffic, or address differences make interpretation
inconclusive; the runner retains the independent probe outcome separately.

Capture is at most 12 seconds, at most 64 packets per point, with a 20-second
external process watchdog and a two-second forced-kill grace. The helper checks
the supported link types and uses
an Ethernet snapshot limited to the standard IP/UDP headers or a raw-IPv4
snapshot limited to those headers plus at most four bytes of the DNS header.
No question or answer bytes are included. A truncated or unsupported header
fails closed. Tcpdump uses numeric, quiet
output. Raw lines and packet headers remain in process memory,
not logs, files, PCAP, or receipts. The final receipt retains only bounded
outbound/inbound counts, a capture-complete flag, and packet-loss status. No
DNS names, client addresses, payloads, certificates or raw errors are persisted.
Kernel capture drops, early exit, timeout, synchronization failures, output
limits, malformed headers, and packet-limit hits fail closed as incomplete.
The observer reports readiness before the Android query and accepts only an
exact completion marker after it; missing synchronization is inconclusive.

The capture helper and dependencies must be installed by the staging installer,
source-bound and validated in the isolated lab before staging. The owner starts
staging and enters sudo locally; this ADR does not authorize an autonomous VPS
connection. No firewall policy, routes or tunnel configuration are modified by
the observation itself. A successful node-side observation is not DNS/IPv6
leak acceptance: the separate pre-NAT physical-network audit remains required.

## Security and limitations

The fixed-port filter identifies the purpose-built test socket, not arbitrary
DNS. Kernel BPF still evaluates network headers in memory, but no packet bytes or
decoded names are returned or stored. TCP DNS,
DoT, DoH and the system resolver's own traffic are outside this observation.
`inbound=0` is not proof of a drop if capture is incomplete or the NAT changes
the source port. Slot counters may also include a matching concurrent socket;
they are diagnostic, not definitive attribution. Previous receipts are immutable.

The original observer driver collapsed all failures into `complete:false` and
used an eight-second readiness limit, shorter than the observer's own two
three-second interface-discovery limits plus five-second capture startup limit,
even before SSH and staging status overhead. The revised driver records only
allowlisted stage/reason categories and allows 30 seconds for readiness. The
12-second capture window remains unchanged; Android instrumentation that takes
longer is an explicitly incomplete observation, not a valid zero-packet result.
No change in timeouts by itself proves or fixes VPN connectivity.
Tcpdump's linktype listing is read from both bounded stdout and stderr: server
version 4.99.4 emits it on stderr, unlike the local 4.99.6 test environment.
The streams are only parsed in memory and are never retained in the receipt.
The fixed status action also returns, for each timer-live slot, only a
boolean/unknown snapshot of whether the kernel quota object is exhausted.
It does not expose its live byte count, alter the gate, or replace the final
closed passed counter used for settlement. The status and quota reads are not
atomic with window rotation; errors are unknown, never evidence of remaining
budget. This test-only state helps distinguish a possible exhausted budget
from an apparent DNS problem, but does not by itself prove packet attribution.
The revised driver stores only allowlisted stage/reason categories and bounded,
rounded operation durations; it suppresses raw helper/SSH output. Missing
`tcpdump` or `timeout` fails the observation, not unrelated staging installation.
Locally, the fixed operator and capture were exercised through synthetic
loopback SSH/sudo with a fake Android result; a separate isolated lab used the
real AWG backend and double NAT. Both omit physical Android and VPS behavior.

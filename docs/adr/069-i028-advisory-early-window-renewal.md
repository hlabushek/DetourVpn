# ADR-069 — advisory early renewal for I-028 windows

Status: local implementation; physical Egress observed the advisory request
and passed included connectivity probes, but the latest Egress run failed final
settlement with one unknown receipt and 1 MiB reserved. Subsequent Main receipts
failed once during recovery and once during sustained transfer with byte-quota exhaustion signals.
The latest source-bound Main receipt passed its required suite and recorded
connection generations during sustained transfer, without reproducing the
earlier exhausted-window condition. That pass does not prove the earlier failure
fixed or explain its cause. Physical acceptance remains open. No change to
billing, grants, authorization, node gate, or deployed service.

## Context

The 2026-10-30 physical I-028 Egress receipt shows a timer-live slot whose byte
quota was exhausted before and after the included probe. The client requests
1 MiB windows and renews by elapsed time; native up and a fresh handshake do not
mean the kernel byte quota has headroom. A local real AWG/nft laboratory also
reproduced byte-exhaustion blocking HTTP and UDP DNS while the timer remained
live. ADR-040 describes 16 MiB, whereas the tested implementation defaults to
1 MiB. Raising the default changes funding reservations, excludes accounts
with smaller available credit, and does not prevent fast exhaustion.

## Decision

Use the existing AmneziaWG backend's aggregate peer transfer statistics as an
optional *advisory* signal to ask for an existing, fully authorized v3 renewal
once the estimated transferred bytes since the previous renewal reach half the
granted budget. The application worker reads only a cumulative aggregate, never
peer identifiers or payloads. The optional adapter capability is named
`traffic-stats-advisory.v1`. No byte count is sent to the API or used as a
ledger measurement. Missing, invalid, or reset counters establish a fresh
baseline or fall back to the existing time-based renewal.

The baseline for the next grant is sampled before the prior renewal request so
traffic in flight around rotation is conservatively counted in the new estimate.
Only one idempotent renewal intent can be outstanding, including retries after
lost responses. The existing grant, funding, node generation, native deadline,
revocation, replay, and settlement checks remain authoritative. Receipt bytes
remain the final closed kernel outer UDP counter, not client statistics.

## Limitations and security

This is a best-effort mitigation, not seamlessness or a completeness proof.
AWG counters are not the kernel outer IP quota: headers, unauthenticated
traffic, packet drops, resets, and rotation timing can make them diverge. A
burst can exhaust the quota between polls, and background/Doze can delay polls.
A quota can still be exhausted while the native tunnel reports up; the gate
remains fail-closed. Early requests can temporarily reserve credit sooner and
may hit insufficient available quota; this does not authorize extra access.
The server-side authoritative pressure signal, coordinated protocol capability,
and behavior under sustained high throughput require separate design and
verification if stronger continuity is required. No public readiness or I-028
acceptance follows from local tests. Physical Egress/Main and the separate
pre-NAT DNS/IPv6 audit remain necessary.

## Physical evidence boundary — 2026-10-30

The Egress diagnostic recorded `advisory_traffic_renewal_requested=true` and
passed included staging IPv4/UDP DNS. This proves request creation in that run,
not renewal acknowledgement or sustained continuity. The later source-bound
Main receipt passed its sustained phase through server generation 36 but failed
the recovery probe. A best-effort node snapshot reported the slot quota
not-exhausted before that probe and exhausted after it, alongside IPv4/DNS
timeouts; the samples are not atomic, so quota exhaustion is a strong signal,
not a proven cause. Main cleanup passed, but accounting retained one unknown
receipt and 1 MiB reserved. The current half-budget advisory has therefore not
been shown sufficient for recovery under this physical workload. Do not infer a
fix or increase the product quota from this receipt; investigate the recovery
renewal timing and the unknown settlement before another physical run.

The next source-bound diagnostic records the live gate's bounded remaining
bytes and the current recovery grant's generation, size and remaining lease
time before and after the probe. This should distinguish a grant that reached
its byte ceiling from one whose renewal was not acknowledged. It remains
instrumentation only: the two samples can still race live traffic, and the
authoritative immutable settlement receipt remains the accounting evidence.
The source-bound isolated Linux bootstrap lab passed again against the updated
operator, but explicitly provides no WAN or Android acceptance.

The later Main receipt
`i-028-android-staging-20261030T210227Z-5cd85352.json` failed earlier, during
`sustained_transfer_probe`, with the current slot's kernel byte budget exhausted
while its tunnel handshake and lease remained live. All 13 accounting receipts
settled cleanly and staging cleanup passed. This is evidence of byte-window
pressure, not proof of why rotation did not protect that probe. The 1 MiB
production default remains unchanged: increasing a window changes temporary
funding reservations and is not a general prevention for fast exhaustion. A
bounded test-only diagnostic now captures connection generation, grant size,
and remaining lease during the sustained phase, and current quota/grant state
around each fixed probe. This should localize renewal timing on one prepared
Main run; it does not alter acceptance or claim a fix.

The resulting receipt `i-028-android-staging-20261030T214523Z-d3c48d11.json`
passed the required Main facts and settlement. Across 29 sustained observations,
the active 1 MiB window generation advanced from 3 to 21; sampled probes had
quota headroom. The earlier exhausted-window failure was not reproduced. The
diagnostics establish a successful run, not the cause or correction of the
intermittent earlier exhaustion. The 1 MiB production default remains
unchanged.

The later Egress receipt
`i-028-egress-diagnostic-20261030T231047Z-d4936632.json` passed its included
staging IPv4/UDP-DNS fact but failed final settlement with one unknown receipt
and 1 MiB reserved. The post-probe snapshot showed generation 3 pending and the
slot byte quota exhausted. The test's `included_probe` marker remained set
through disconnect/verification, so the receipt cannot identify whether the
failure occurred in disconnect or settlement. Preserve unknown accounting;
never replace it with a guessed zero. The Egress harness now waits, with a
bounded deadline, for an observed renewal ACK before its ordinary disconnect,
while retaining separate coverage for disconnect racing an acknowledged
renewal. This is a test-scope correction, not a production behavior change.
Physical recheck is still required.

2026-10-31 Egress rerun with source-port audit
`i-028-egress-diagnostic-20261031T143401Z-5344375a.json` passed initial
included staging IPv4/UDP-DNS facts and received renewal ACK generation 12,
then failed at `egress_hold_refresh_probe`: the IPv4 probe did not confirm
staging egress and both its primary and fallback DNS attempts timed out. The
sanitized node snapshots showed 3,004 bytes remaining before the probe and
zero after it; the live connection still had 65 seconds on its lease. Cleanup,
settlement, phone restoration, independent postcheck, and timeout-timer disarm
passed. This correlates the failure with quota exhaustion near the probe, but
does not establish why the controller did not preserve usable quota or egress.
Do not increase the 1 MiB client default as a workaround: ADR-040 funding
semantics remain unchanged. The operator's synchronized AP capture
`i-028-packet-audit-included-source-port-20261031T1431Z.json` saw 5,529 outer
UDP packets, zero fixed-probe source-port packets, and nonzero aggregate DNS
and IPv6. Because the included IPv6 probe failed DNS before opening its socket,
its zero source-port count is not a leak-free result. The run remains failed
and does not close I-028.

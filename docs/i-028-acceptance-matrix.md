# I-028 acceptance matrix — 2026-10-25

Only I-028 is assigned. Historical failed receipts remain unchanged. A passing
component receipt does not make the full issue or an earlier runner pass.
ADR-072 supersedes older dated notes below that list the broad DNS/IPv6 audit
as an open requirement for the owner's personal I-028 checkpoint.

## Current status — 2026-11-01

Earlier I-019 receipt `evidence/i-019-attempt-5b914fde876e4c0e866bcce4729835f8.json`
failed at B reconnect despite the DNS-free trigger: four HTTPS probes reported
`dns`, with no fresh native handshake. The literal connect outcome was not
recorded, so its network cause is unknown. A cleanup was confirmed; the
operator stopped B and root recovery returned `recovery_not_needed`. Read-only
checks found endpoint failed, agent inactive, timer/namespace/listener absent,
and forwarding=0. Host cleanup is confirmed. A later passing provider receipt
below supersedes this failed attempt; keep it unchanged as failure history.

Earlier I-019 receipt `evidence/i-019-attempt-d16cc96c0cd54f59b940d98e4331bb41.json`
failed at B reconnect without a fresh handshake. Its `other` probe category
corresponds to `dns`, the sole fixed probe result missing from the sanitizer;
the DNS and handshake failure cause remains unknown. The sanitizer was fixed
and six diagnostic tests passed. A cleanup passed; subsequent operator recovery
and read-only checks confirmed B stopped and forwarding restored to 0. The
independent-provider gate remains open.

The latest source-bound Egress diagnostic
`evidence/i-028-egress-diagnostic-20261031T182523Z-91d07c8e.json` passed: all
six scoped facts are true, including the included fixed-IPv6 block. Exact
settlement was 32,772,659 bytes over 32 receipts, with zero reserved, unknown,
or open bytes and zero ledger violations; phone, staging, journal,
temporary-grant, timer, and independent post-run cleanup checks passed. The
receipt remains `diagnostic_only` with `vpn_dataplane_acceptance=false`.

The correlated owner-provided AP-station capture
`evidence/i-028-ipv6-included-block-20261031T182345Z.json` saw the VPN outer
trigger and completed the full 90-second window without packet-limit truncation.
It observed 9,898 VPN outer UDP packets and zero packets from the included
fixed-IPv6 probe source port; the receipt confirms a successful direct same-port
control and that the included socket reached `connect()`. Four aggregate IPv6
packets remain unattributed. This passes the bounded fixed-socket criterion in
ADR-071, not a device-wide or comprehensive DNS/IPv6 leak audit. By owner
decision ADR-072, no further DNS/IPv6 audit is required to finish this personal
I-028 checkpoint; the broader audit remains a separate product-release concern.

The physical matrix below already records passing receipts for forced Doze,
natural sleep, Wi-Fi/mobile transitions, system VPN-permission revoke,
disconnect during renewal, and two simultaneous devices. These do not need to
be rerun for this stage. The latest Egress receipt's `open_cases` array is a
stale template: it repeats several of those already-passed cases and is not an
authoritative outstanding-work list.

The owner has superseded the earlier ADR-071 choice: for included full-tunnel
traffic, the bounded staging checkpoint requires a direct fixed-IPv6 positive
control with VPN off and a fixed-literal connection failure while the included
VPN is active. If Android returns a generic `io` category, the direct control
must use the same local port, the included probe must confirm bind succeeded and
connect() was attempted, and a synchronized pre-NAT AP capture must show the
tunnel trigger, a complete untruncated 90-second window, and zero packets from
that fixed IPv6 source port. The earlier 18:05 capture below was inconclusive
due to its source-port mismatch; the later same-port run above supersedes it for
this scoped criterion. The comprehensive DNS/IPv6 packet audit is removed from
this personal checkpoint by ADR-072; it is not claimed as passed and remains a
separate product-release concern. At the time of the attempts listed below,
I-028 remained open for independent-provider failure. ADR-073 approves Xorek
`150.241.99.227` as B; its fingerprint and
read-only host baseline were verified. The earlier most-complete physical
attempt on 2026-11-01
(`evidence/i-019-attempt-d17af2fd6b494e42a769f2ab53493771.json`) passed initial
B/A egress, A failure cutoff, both deadline/recovery checks, and
ordinary-network restoration, then failed at B reconnect without a fresh
handshake/egress; final ledger verification was not reached. The next attempt
(`evidence/i-019-attempt-627387de1c87477bac1f3f71de8f5ca0.json`) passed a fresh
B handshake but failed before recording B egress; settlement was 48,248 bytes.
The latest attempt (`evidence/i-019-attempt-5dcc929ff4cd47ae8fe3b58fb794773b.json`)
also passed B handshake/recovery, then failed before recording B egress;
settlement was 51,416 bytes. Root cause: the receipt capped probe records at 16,
and the API rejected record 17. The fixed cap is now 32, with regression tests
for accepting record 17 and rejecting record 33. Five targeted diagnostics tests
and Ruff passed. After operator stop/recovery, read-only checks confirmed both
hosts cleaned and forwarding restored to A=1/B=0. This does not pass the drill;
the cap fix needs a fresh physical run and bounded grants.
Completed lifecycle, network-transition, permission-revoke,
renewal-disconnect, and two-device scenarios do not need another run.

2026-11-01 final independent-provider update: receipt
`evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json` passed all 15
facts, fresh A/B egress, both recovery paths, four settlements, two full
lost-counter debits, zero reserved bytes, and cleanup on both hosts. This
passes the bounded provider-failure criterion by distinct origin ASN only;
full upstream independence and automatic failover are not proven. Physical
ADR-070 established-session status also passed in the Egress receipt cited
below; the broad packet audit remains deferred under ADR-072, not passed.

## Superseding included IPv6 probe evidence — 2026-10-31

The physical receipt
`evidence/i-028-egress-diagnostic-20261031T180447Z-6fd79d63.json` records a
successful direct IPv6 literal control with VPN off. In the included Egress
probe, the VPN was validated, staging IPv4 and the fixed UDP-DNS control worked,
and the same IPv6 literal did not connect; Android reported the bounded
category `io`, so the suite correctly retained `passed: false`. The direct
control used port 42430 and the included probe used port 42429; the probe also
did not record whether bind succeeded before connect(). The owner-pasted
synchronized AP capture is preserved in
`evidence/i-028-ipv6-included-block-20261031T180553Z.json`: it saw the VPN
outer trigger, completed 90 seconds without reaching the packet limit, and saw
zero packets from the fixed IPv6 socket's source port 42429. This supports
the hypothesis that the included probe was blocked, but does not exclude a
local bind failure. The capture also saw 186 aggregate global IPv6 packets;
their app/UID attribution is unknown, so this is not evidence of device-wide
IPv6 blocking or leak freedom. Accounting and cleanup in the physical receipt
passed. A same-port direct control and a recorded connect-attempt flag were
added; the successful synchronized run is recorded in the current status above.

## Superseding focused status — 2026-10-31

The latest owner Egress receipt
`evidence/i-028-egress-diagnostic-20261031T152004Z-5995667a.json` passed its
scoped checks, exact accounting and cleanup. Ten bounded hold observations had
handshake age above 60 seconds and recent inbound progress while native up,
network and lease were valid, but `client_phase` stayed `reconnecting` during
pending renewal. Root cause: the pending-renewal branch returned before the
ADR-070 established-session calculation. The local fix now preserves the
initial fresh-handshake barrier and evaluates the existing bounded inbound
freshness rule during pending renewal without extending the native lease.
Targeted controller tests pass 25/25; source-bound physical verification is
still required.

The same run's synchronized included capture
`evidence/i-028-packet-audit-included-source-port-20261031T1515Z.json` saw
7,808 outer UDP packets, zero fixed DNS/direct-IPv6 probe-port packets and five
unattributed aggregate IPv6 packets. Together with the positive-control
baseline `evidence/i-028-packet-audit-source-port-baseline-20261031T1418Z.json`,
this validates only the controlled probe flows. A local Egress harness change
now adds an excluded same-UID fixed DNS/IPv6 probe after the included window;
it has not yet been built or physically verified. Excluded, transition and
outage captures remain open, as does independent-provider failure (no approved
independent target is recorded).

The physical receipt above predates both local follow-ups. Its included probe
had working staging IPv4/UDP DNS but IPv6 unavailable because its DNS lookup
failed; its AP capture saw five unattributed aggregate IPv6 TCP/443 packets.
The subsequent direct phone Probe passed IPv4, IPv6 and DNS after VPN teardown.
Therefore the physical Egress pass is useful transport/renewal/cleanup evidence,
but does not verify the pending-renewal UI fix or close comprehensive DNS/IPv6
acceptance. The current source-bound Build attempt stopped at Android APK
generation because the laptop C: drive lacked free space; current source is not
ready for staging. Focused local checks now pass: 25 Flutter controller tests
and 26 packet-audit tests.

## Current diagnostic update — 2026-10-31

Latest physical Main receipt (source-bound at measurement)
`evidence/i-028-android-staging-20261031T122302Z-4de68407.json` passed in
476.07s with all 11 required Main facts true. At measurement time its 11 source
hashes matched. Later Egress-only diagnostic edits changed two shared test
harness files (`apps/client/integration_test/i028_live_test.dart` and
`services/api/tests/test_i028_live.py`), so the receipt is now historical for
its measured source set (9/11 hashes still match); production sources in that
receipt are unchanged. Exact settlement was 38,768,559 bytes over 45
receipts, with zero reservations, unknown receipts, open connections, or ledger
violations. Cleanup, timer disarm, grant removal, journal preservation, phone
restoration, and independent post-check passed. This is a source-bound Main
pass, not full I-028 dataplane acceptance. Summary:
`evidence/i-028-main-physical-followup-20261031.json`.

The local source-bound Main Build passed Flutter analysis, all 102 Flutter
tests, and Android debug/instrumentation APK builds with 95/95 declared inputs
matching; details are in `evidence/i-028-main-build-followup-20261031.json`.

Latest successful physical Egress diagnostic for its measured source set
`evidence/i-028-egress-diagnostic-20261031T120213Z-ca62ed0e.json` passed.
The included same-UID staging IPv4/UDP-DNS probe succeeded, renewal was
acknowledged at active generation 22, and a later same-UID staging IPv4 probe
also succeeded. Exact settlement was 20,203,511 bytes over 22 receipts, with
zero reservations, unknown receipts, open connections, or ledger violations.
Cleanup, timer disarm, grant removal, journal preservation, phone restoration,
and independent remote post-check passed. This closes this scoped Egress run
only; the receipt itself sets `vpn_dataplane_acceptance=false`.
The auxiliary DNS-path observer completed but returned no probe response and
zero packets at either capture point. The independent same-UID fixed UDP-DNS
probe passed, but this auxiliary result is not positive path-capture evidence
and does not establish a leak.

The subsequent source-bound physical Egress run
`evidence/i-028-egress-diagnostic-20261031T130117Z-023d007d.json` failed at
`included_probe`: staging IPv4 worked through the fallback, but the fixed UDP
DNS probe timed out and the IPv6 result was `dns`, so IPv6 blocking was not
proved. A separate synchronized DNS observer later returned a response with no
packet loss. Settlement matched exactly at 1,876,236 bytes over three receipts;
all cleanup checks passed, including timer disarm and independent post-check.
The 90-second AP capture saw 1,083 VPN endpoint packets, one direct IPv4 DNS
packet, and 178 global IPv6 packets. Because this test failed before the planned
140-second hold, the aggregate capture may include packets after disconnect;
with no packet timestamps or UID attribution it proves neither leakage during
VPN nor a clean included window. The operator was unsure whether IPv6 became
unavailable only at the end; no immediate phone-only post-test probe was
available. See `evidence/i-028-egress-diagnostic-followup-20261031i.json` and
`evidence/i-028-packet-audit-included-20261031T1302Z.json`.

The local Egress harness now defers only this exact DNS-timeout case when the
target UID, validated VPN, staging IPv4, and timeout categories are confirmed.
It records no success fact, completes the existing bounded hold and normal
settlement, then still fails the suite. Other probe failures remain fail-fast.
The updated Egress source-bound Build passed with 95/95 input hashes, Flutter
analysis/unit tests, all three physical entrypoint compilations, and Android
Gradle; 59 Python diagnostic/packet-audit tests passed. Updated client APK SHA-256
is `7357b5b62198add778ad3a1a8cf594f35ee04dfc061d0100b9f0aa8f829561aa`.
No physical run has used this build yet. Evidence:
`evidence/i-028-egress-diagnostic-followup-20261031i.json`.

The following owner-run Install safely archived the preceding closed run but
stopped at the disk preflight (installer line 52: available space must exceed
1,500,000 KiB). The archive helper verified closed resources and timer disarm;
the new staging runtime/timer was not created and Test did not start. This is a
host-capacity blocker only. See
`evidence/i-028-install-preflight-20261031T1316Z.json`.

ADR-070 and the local client update address the phase discrepancy: initial
connection still needs a fresh handshake; an established session may remain
`connected` for up to 60 seconds after observed inbound progress, conditional
on native up, validated network and valid lease. TX-only or unavailable stats
do not confirm activity. Targeted Flutter tests, a clean source-snapshot full
Flutter analyze/test/build, Android unit tests/lint, and the refreshed
source-bound Egress runner build passed. See
`evidence/i-028-local-followup-20261031f.json`. The clean snapshot avoids the
truncated analysis-server response observed when analyzing directly from the
OneDrive checkout. The fresh physical Egress run passed; however, its hold
handshake age was 36 seconds, so it does not demonstrate the inbound-counter
fallback after a stale handshake. See
`evidence/i-028-egress-status-followup-20261031.json`.

The source-bound Main runner was built before the two Egress harness-only
changes described above: Flutter analysis and all 102 Flutter tests passed,
Android debug/instrumentation APKs built, and all 95 declared inputs matched.
Its physical Main Install/Test subsequently passed in 476.07s with 11/11
required facts, exact settlement and independent cleanup; see the receipt
summary above and `evidence/i-028-main-build-followup-20261031.json`. Those
historical Main results remain valid for the measured source set, while the
fresh Egress build below is the current physical artifact set.

The local Egress runner now retains a bounded 140-second status series, with a
single fixed same-UID IPv4 probe during the hold to observe inbound progress.
It records only elapsed seconds, client phase, native up/network, handshake age,
lease time, counter availability and whether RX advanced within 60 seconds.
The sanitized `adr070_stale_handshake_connected_with_recent_inbound` result is
diagnostic and does not alter Egress suite pass/fail. It becomes evidence only
after a source-bound physical run actually observes handshake age over 60,
recent inbound progress, and connected status together.

After adding bounded `fixed_probe_attempted` / `fixed_probe_succeeded` fields,
the source-bound Egress Build was rerun at
`2026-10-31T12:44:02Z`: 95/95 declared inputs matched, Flutter analyze passed,
all 102 Flutter tests passed, the seed/egress/cleanup entrypoints compiled,
the independent UID probe APK pair built, and Gradle reported `BUILD SUCCESSFUL`.
Ruff and Dart formatting passed; the diagnostic-sanitizer tests passed 36/36,
the packet-audit tests passed 23/23, and `git diff --check` passed.
Client APK SHA-256 is
`1b56ede17de9868bf66c4467bde23490181de19c650e270cf0c76a00f9a75d68`; probe
test/base APK SHA-256 values remain
`c27c0612bf03ccda51cfbd880c6f4f0a9b1f7d4697d551bcf531ac7266c3eecb` and
`61006df375b245a808b779bdb5cbb02e1bfa44460737d0b61fe982f99a93481c`.
Receipt: `evidence/i-028-local-followup-20261031h.json`. This supersedes the
older local Egress artifact hashes; no physical Install/Test was run with this
fresh build. The successful Main and Egress physical receipts above each differ
from the current checkout only in those two runner/sanitizer files; a fresh
Egress physical run is still required to bind the new diagnostic fields and
observe ADR-070 on-device.

Keep Android DNS on Automatic; it was not changed. Although the latest Main
receipt's `api6.ipify.org` lookup failed as `dns` both with and without VPN
while fixed UDP DNS succeeded, a fresh phone-only Probe at
`2026-10-31T12:39:34Z` now passes: direct IPv4, direct IPv6, fixed UDP DNS,
validated Wi-Fi, no active VPN, and temporary APK cleanup all succeeded. This
restores the direct-baseline precondition for the next audit, but is not itself
a packet capture or VPN dataplane acceptance. Receipt:
`evidence/i-028-probe-after-startup-fix-20261031T123945453Z.json`.

The hold checkpoint recorded `client_phase=reconnecting` even though
`client_has_error=false`, `native_up=true`, `native_network=true`, the renewal
ACK was active, and the lease had 80.55 seconds remaining. The native handshake
age was 73 seconds, and the same-UID staging probe succeeded. This exposed a
client-status inconsistency; ADR-070 and the local change above address it.
The new physical run observed `connected` at handshake age 36 seconds, which
does not exercise the stale-handshake fallback. The older observation is not
evidence that the tunnel's data path failed.

Correlated triggered AP capture
`evidence/i-028-packet-audit-included-20261031T1116Z.json` completed the full
90-second post-trigger window with 6,026 VPN endpoint UDP packets, zero direct
IPv4/IPv6 DNS packets, zero global IPv6 packets, and no packet-cap hit. It was
run alongside the physical Egress test, but exact process start/end timestamps
and UID attribution are unavailable. This is a bounded AP-station observation,
not a UID-level leak-freedom pass or comprehensive I-028 dataplane acceptance.

Preceding source-bound physical Egress diagnostic
`evidence/i-028-egress-diagnostic-20261031T105725Z-e4575a58.json` failed at
`egress_hold_client_not_connected`. The staging IPv4/UDP-DNS probe and a later
same-UID IPv4 staging-egress probe both passed; server renewal ACK was active at
generation 9. The failure is therefore in the additional Flutter phase
condition, not an observed loss of staging egress. The receipt lacks a native
snapshot at the exact failure point, so the reason the controller phase did
not return to `connected` remains unresolved. Settlement and cleanup passed,
including timer disarm, grant removal, journal preservation, and independent
post-check.

The correlated capture `evidence/i-028-packet-audit-included-20261031T1057Z.json`
completed the full 90-second post-trigger window with 2,664 VPN endpoint UDP
packets, zero direct DNS packets, and one global IPv6 TCP/443 packet. Test and
capture elapsed times align closely, though exact start timestamps are absent.
The AP-station filter cannot attribute that IPv6 packet to the probe UID; this
is unresolved, not a leak finding or a clean UID-level pass. The local harness
was adjusted to require ACK, native up/network/lease, and successful same-UID
staging egress in the hold, while preserving the initial connected barrier and
recording later client phase/native state. The subsequent physical Egress pass
is recorded above; local code now handles the status case, with physical
verification still open.

Latest source-bound physical Egress diagnostic
`evidence/i-028-egress-diagnostic-20261030T235320Z-3d00bcfe.json` passed.
The real staging IPv4/UDP-DNS probe, fresh handshake, and active ACK for renewal
generation 2 passed. Exact settlement was 1,064,788 bytes over two receipts,
with zero reserved bytes, unknown receipts, open connections, or violations.
Phone/staging cleanup, timer disarm, grant removal, journal preservation, and
independent remote post-check passed. Receipt source hashes matched the
checkout at the time of the run; a subsequent local test-only capture-hold
change is recorded separately below. This is a scoped Egress pass, not full
I-028 or VPN dataplane acceptance.

Correlated triggered AP capture
`evidence/i-028-packet-audit-included-20261030T2352Z.json` completed its full
90-second post-trigger window with 703 outer UDP packets and no packet-limit
hit. The trigger wait was 118.31 seconds, while the concurrently started Egress
Test took 133.65 seconds; the capture therefore continued about 75 seconds
after the Test process and is not a 90-second active-VPN acceptance window. It
also observed four direct IPv4 UDP-DNS and 250 global IPv6 packets on the AP
station. Since filtering is station-level rather than UID-level, and much of
the capture may be post-test, those packets are unresolved: they establish
neither a clean probe-UID pass nor an application leak. Operator-reported IPv6
availability after the run is not independently probed and does not resolve
this attribution.

Local follow-up `evidence/i-028-local-followup-20261031b.json` adds a 100-second
post-ACK active-tunnel hold to the Egress test, with five-second native/server
checks and a 30-second bounded ACK wait for any renewal during the hold. The
source-bound Egress Build, Flutter analysis, 99 Flutter tests, and app/
instrumentation APK compilation passed with zero source mismatches. The physical
test with this timing correction remains unrun.

The operator's earlier report of IPv6 loss until Wi-Fi reconnect and later
report of IPv6 remaining available are both retained as unverified observations;
neither is attributed to VPN/DNS behavior.

Latest source-bound physical Main receipt
`evidence/i-028-android-staging-20261030T214523Z-d3c48d11.json` passed the
Main suite with all 11 required facts. The receipt's `test_i028_live.py` source
hash matches this checkout. It recorded 29 sustained-window observations;
the active 1 MiB grant advanced from generation 3 to 21, while the sampled
before/after fixed probes retained quota headroom. This did not reproduce the
earlier exhausted-window failure and does not prove that its cause is fixed.
Exact accounting settled 23,033,880 bytes across 29 receipts, with no reserved,
unknown, open, or violating ledger entries. Phone/staging cleanup, timer
disarm, temporary grant removal, private-journal preservation, and independent
remote post-check passed. The receipt itself still marks
`vpn_dataplane_acceptance=false`. The issue-wide open gates are independent
provider failure and comprehensive DNS/IPv6 packet capture; earlier scoped
receipts cover lifecycle, Wi-Fi/mobile, system permission revoke, and two-device
scenarios.

Previous physical Egress diagnostic
`evidence/i-028-egress-diagnostic-20261030T231047Z-d4936632.json` failed
after the included probe. The receipt records the required staging IPv4/UDP-DNS
fact as true, but final control-plane settlement failed: ledger consistency was
true with zero violations and zero open connections, while one receipt remained
unknown and 1 MiB remained reserved. Cleanup, timer disarm, phone restoration,
and independent remote post-check passed. The Flutter stage marker remains
`included_probe` through disconnect and final `/verify`, so it does not identify
the failing operation; the settlement fields identify the concrete failed
acceptance check. The receipt's post-probe node snapshot also shows slot 1's
byte quota exhausted and generation 3 pending. This is consistent with an
unsettled/pending window, but does not prove why its final observation was
unknown.

The correlated triggered AP capture is
`evidence/i-028-packet-audit-included-20261030T2310Z.json`. It saw the fixed
outer VPN endpoint trigger and 1,225 UDP packets to slots 51901/51902. It also
counted five global IPv6 TCP/443 packets and zero direct DNS packets. The
capture stopped at its packet limit before the 90-second window ended. Because
the filter selects the phone's AP station rather than the test UID, those five
IPv6 packets are an unresolved signal, not proof of an app DNS/IPv6 leak. This
is not a complete included-phase audit or a passing Egress receipt.

Local follow-up `evidence/i-028-local-followup-20261031.json` records the
Egress runner's monotonic 30-second deadline for renewal ACK and the 20,000
packet audit cap. Targeted packet/API tests, Ruff, Flutter analyze, 99 Flutter
tests, and the source-bound Egress Android app/instrumentation build passed.
The subsequent scoped physical Egress run and complete-window capture are
recorded above; full DNS/IPv6 UID attribution and other issue-wide gates remain
open.

Earlier physical Egress diagnostic `evidence/i-028-egress-diagnostic-20261030T192500Z-db44b82d.json`
passed after Install/Test. The same probe UID had direct Wi-Fi IPv4 and fixed
UDP DNS before VPN, then staging IPv4 and fixed UDP DNS through VPN. The separate
fixed-port node DNS-path observation completed with a response and one packet
in each direction at both tunnel and uplink points. Two settled receipts totaled
876,852 exact bytes with zero reservation or ledger violations. The test-only
traffic advisory requested an early renewal before its time threshold; the
receipt does not prove node acknowledgement or uninterrupted traffic. Sampled
quota states were not exhausted but are non-atomic. Phone/staging cleanup,
temporary grant removal, timer disarm and independent remote post-check passed.
This is diagnostic-only: `vpn_dataplane_acceptance` remains false. Both IPv6
probe failures were DNS failures, so this run does not prove IPv6 blocking or
absence of DNS/IPv6 leaks. The receipt still lists lifecycle, transition, revoke,
in-flight renewal disconnect, two-device, independent-provider and comprehensive
DNS-capture cases as open.

Earlier physical Egress diagnostic
`evidence/i-028-egress-diagnostic-20261030T224336Z-f7796385.json`; its scoped
runner passed, including staging IPv4/UDP-DNS and cleanup. The separate
node-side DNS-path observer completed but saw no packets or response. The
operator-labeled 120-second AP capture supplied afterward is recorded in
`evidence/i-028-packet-audit-operator-window-20261031.json`: it saw 23 direct
IPv4 DNS packets, 303 global IPv6 packets, and zero packets to the fixed tunnel
UDP ports. It has no timestamp or phase trigger; the Egress Test took 146.12
seconds, so overlap with its included probe is unknown. This is an unresolved
diagnostic, not evidence of a leak or a clean included phase. The operator also
reported that global IPv6 connectivity is unavailable after tests until Wi-Fi
is reconnected; this has not yet been independently measured immediately after
a test, so its scope and cause are unknown. Preserve the state and run the
phone-only direct Probe before reconnecting Wi-Fi on the next occurrence.
The local triggered-capture implementation now discards pre-trigger headers,
reads the pipe without buffering delays, and includes capture/trigger UTC
timestamps labelled as capture-host wall clock. Its 23 synthetic tests, Ruff
check, and format check pass; this is tooling verification only, not a physical
packet result. See `evidence/i-028-packet-audit-tooling-20261030T225537Z.json`.

Previous source-bound physical Main
`evidence/i-028-android-staging-20261030T201453Z-c301d674.json` failed at
`recovery`, after recording included staging IPv4/UDP DNS, three minutes of
sustained transfer with acknowledged generation 36, same-UID exclusion,
disconnect/revoke, and API/agent deadline closure. The recovery probe timed out
for HTTPS and UDP DNS while the Android VPN remained up. Best-effort node state
changed from quota-not-exhausted before the probe to exhausted afterward; this
is a strong quota-pressure signal, not atomic proof of the failure cause. Its
post-cleanup accounting still had one unknown receipt and 1 MiB reserved, so
settlement failed despite ledger consistency. Phone/staging cleanup, timer
disarm and independent remote post-check passed. The run used source hashes
matching the current checkout; preserve its failed verdict.

The subsequent diagnostic Main run recorded bounded remaining quota and current
grant generation, size and lease observations during sustained transfer and
around its probes. It passed without reproducing the exhausted-window condition.
This is a successful current Main receipt, not proof of the earlier failure's
cause or of issue-wide acceptance; preserve the failed receipt unchanged.

The local isolated bootstrap lab was rerun after Stage rejected its stale
operator-source hash. It passed with the current `runtime.py` hash; its receipt
explicitly keeps `wan_or_android_acceptance=false`. This validates only the
local staging prerequisite, not Android recovery or physical Main acceptance.

Physical Egress `evidence/i-028-egress-diagnostic-20261030T171946Z-96824d60.json`
passed its scoped checks: included staging IPv4 and UDP DNS, six measured
settlements and cleanup. Its separately observed fixed-port DNS probe timed out
with zero matched packets; `passed=true` does not imply that probe succeeded or
that a DNS/IPv6 audit passed. Before/after included quota snapshots were exhausted
and not exhausted respectively, but do not prove why a renewal was requested.

Physical Egress `evidence/i-028-egress-diagnostic-20261030T165220Z-08f1e933.json`
failed at included traffic while both sampled slot quota states were exhausted.
The revised DNS observer completed, with no matching packets at either capture
point and no DNS response; snapshots are not atomic with window rotation. Two
closed receipts measured 2,096,176 bytes and settlement/cleanup passed, with
operator-reported timer disarm and an independent resource postcheck. ADR-069
adds a local native-traffic advisory for early renewal; it cannot prove
uninterrupted traffic or replace the authoritative node byte meter, and awaits
physical verification.

The newer `evidence/i-028-egress-diagnostic-20261030T163444Z-d9e3b57c.json`
failed because the DNS observer rejected its linktype during readiness. An
owner-run isolated server check found tcpdump 4.99.4 outputs `-L` listings to
stderr; the observer previously read only stdout. This is a confirmed
compatibility defect and a strong explanation of the physical failure; its live
AWG listing was not retained. The fix is not physically rechecked. That receipt
also records successful included IPv4/DNS probes, not-exhausted quota snapshots,
clean settlement and cleanup, and operator-reported timer disarm with an
independent resource postcheck. It does not establish the earlier VPN failure.

The newer `evidence/i-028-egress-diagnostic-20261030T162428Z-867ee863.json`
failed at the fixed DNS observation stage and does not contain its result.
The preceding physical probe did demonstrate staging IPv4 egress and UDP DNS;
the quota-exhausted snapshots were false both before and after it, and the one
settled window recorded 51,580 bytes. Direct IPv6 was inconclusive due to a DNS
failure. Cleanup and operator timer disarm were reported successful, and the
independent remote resource postcheck passed. This does not establish the
cause of earlier failures or close the packet audit.

The current client requests 1 MiB per time-renewed window, unlike ADR-040's
documented 16 MiB. Two measured windows in the earlier failed Egress receipt
total 2,095,354 bytes (1,798 bytes below 2 MiB). A real AWG/nft isolated lab
reproduced a live timer with exhausted quota and blocked HTTP/DNS. This makes
budget exhaustion a strong physical hypothesis, not a proven root cause. The
new test-only quota-exhaustion snapshot is best-effort and non-atomic with
window rotation, and its quota `used` value is not a final settlement meter.

The earlier short physical `Egress` receipt,
`evidence/i-028-egress-diagnostic-20261030T141643Z-51022427.json`, is failed:
the original HTTPS and later fixed TCP checks timed out while the fixed UDP DNS
probe responded. The separate node-side DNS observer was incomplete, with no
recorded failure category; it produced no usable packet counts. Phone/staging
cleanup, timer disarm reported by the operator, and the independent unprivileged
resource post-check passed. No root cause or VPN dataplane acceptance follows.

Earlier fields named `baseline_ipv6_available_included_ipv6_blocked` are not
proof of blocking: an IPv6 hostname failure can itself be DNS failure. Historical
receipts are not rewritten. The separate ADR-064 packet audit remains open.
The two-device receipt from 2026-10-29 remains a scoped pass only; independent
provider failure and comprehensive DNS/IPv6 capture remain open.

Local follow-up corrected the observer driver's readiness-time budget and added
allowlisted failure stages and rounded durations. Synthetic local transport
tests exercise Windows → WSL → loopback SSH → sudo → fixed operator; isolated
AWG/NAT labs exercise the observer's fixed operator and packet counts. These
local results are not physical Android or VPS acceptance.

| Criterion | Verification | Evidence | Current status |
|---|---|---|---|
| Same account/family/device after process restart | Actual different Android PID | `evidence/i-028-main-success.json` | Passed in single successful main run |
| Native handshake, independent UID IPv4/DNS | Fixed probe through A, VPN DNS metadata | `evidence/i-028-main-success.json`; `evidence/i-028-android-staging-20261031T122302Z-4de68407.json` and earlier receipts | Passed in the latest source-bound Main run: fresh handshake and included staging IPv4/UDP-DNS. |
| Established-session status after handshake age exceeds 60s | Native RX progress within 60s, UI `connected`, native up/network and valid lease in the same sample | ADR-070; `evidence/i-028-egress-diagnostic-20261031T165654Z-85a5fc2c.json`; local controller regression tests | Passed in the latest source-bound Egress run: 25 observations, recent inbound traffic, client connected and native/network/lease valid above 60s handshake age (maximum 119s). |
| Sustained renewal and actual traffic >185s | Periodic independent egress, generation advancement | `evidence/i-028-main-success.json`; `evidence/i-028-android-staging-20261031T122302Z-4de68407.json` and earlier receipts | Passed in the latest source-bound Main run; all sustained-renewal facts were true. |
| Included/excluded same UID | Compare real direct and staging egress | `evidence/i-028-main-success.json` | Passed |
| Disconnect and remote revoke | Native down, server close and measured settlement | `evidence/i-028-main-success.json` | Passed |
| API+agent failure, restart/reconcile | Native/kernel deadlines, new real connection | `evidence/i-028-main-success.json` | Passed |
| Exact accounting | Every measured receipt settled; zero holds/unknown/open/violations | `evidence/i-028-main-success.json`; `evidence/i-028-android-staging-20261031T122302Z-4de68407.json` | Passed in latest source-bound Main: 45 receipts, 38,768,559 bytes, zero reserved/unknown/open/violations. |
| Corrected timer cleanup | Real A cleanup plus independent post-check | `evidence/i-028-fixed-cleanup-staging.json` | Scoped pass only |
| Background/screen off/forced Doze and wake | Observe native deadline and server gate across each interval, then real recovery | `evidence/i-028-lifecycle-staging-20261024T225819Z-2a9ff221.json` | Passed in one full physical Lifecycle run: three separate 100-second observations, real recovery, phone/A cleanup and independent post-check |
| Natural sleep with USB detached | New unforced deep-idle, zero live A slots/peers, native down after wake and new real staging egress | `evidence/i-028-natural-sleep-staging-20261025T192620Z-286a330b.json` | Passed on one Pixel: 392 seconds without USB, natural deep-idle observed, native down after wake, new staging egress/window recovery; exact settlement and phone/A cleanup passed |
| Wi-Fi/mobile both directions and temporary loss | Positive transport baseline, real traffic after each transition, deadlines/client state | `evidence/i-028-network-revoke-success.json` | Passed in physical run: real Wi-Fi→cellular→loss→Wi-Fi transfer, radio-loss confirmation and valid windows |
| System settings VPN permission revoke | Owner revokes in Android Settings; native down, server settlement, no reconnect | `evidence/i-028-network-revoke-success.json` | Passed in physical run: app-op revoke, native down, server close, zero holds/unknown and 40-second no-restart observation |
| Disconnect during renewal | Delay real accepted renewal response and wait for actual node ACK; disconnect before release; inspect native and server settlement | `evidence/i-028-main-success.json` | Physical pass, zero holds/peers; native stays down |
| Comprehensive DNS/IPv6 leak audit | Positive direct IPv6 baseline and scoped pre-NAT observation across transitions/failures | ADR-064; ADR-071; `evidence/i-028-egress-packet-audit-20261031T1652Z.json` | Deferred by owner decision for the bounded personal staging checkpoint; not passed. The latest source-bound Egress capture saw the included VPN outer flow and no fixed probe DNS/IPv6 packets, while excluded positive controls were visible. It does not prove UID-wide leak-freedom. Do not claim leak protection or release readiness. |
| Two physical devices simultaneously | Distinct local keys/windows/counters, cross-owner denial/revoke isolation | `evidence/i-028-two-device-staging-20261029T101845Z-b0085c2a.json` | Passed: two physical devices used distinct windows/slots and staging egress; cross-owner revoke was denied; B remained active after A disconnected; exact settlement and staging/device cleanup passed. The 09:52Z receipt remains preserved as failed because its final aggregate treated the expected empty resource-marker list as false; the corrected aggregation is covered by a regression test. |
| Independent provider failure | Approved independent node, explicit manual recovery, fresh egress, exact settlement and cleanup | ADR-073 authorizes `45.10.166.106` + Xorek `150.241.99.227`; `evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json` | Passed bounded on 2026-11-01: 15/15 facts, fresh A/B egress, both recovery paths, four settlements, two full lost-counter debits, zero reserved bytes, and cleanup on both hosts. Distinct origin ASNs are verified; full upstream independence and automatic failover are not. |
| Source-bound Main suite with corrected cleanup | Source-bound Build/Stage/Install/Test and independent cleanup | `evidence/i-028-android-staging-20261030T214523Z-d3c48d11.json`; `evidence/i-028-android-staging-20261031T122302Z-4de68407.json`; earlier failures remain preserved | Passed in 489.33s and again in 476.07s; latest run had 11/11 facts, exact settlement, and independent cleanup. The broad DNS audit is deferred by ADR-072; the bounded independent-provider gate is recorded above. ADR-070 is accepted by the separate Egress receipt above. |

All eight basic physical criteria above were also repeated successfully in this
single main run, including generation 7, actual periodic transfer and exact
settlement. The older failed receipts remain historical evidence, unchanged.
The main receipt's static open-case list still includes the renewal race despite
its explicit passing facts; future runner reports remove executed cases.

The first lifecycle runner and two earlier network runners retain `passed=false`.
The second network runner timed out after the real system revoke, leaving one
unknown receipt and 16 MiB reserved at failure time. Its cleanup and independent
remote post-check passed; neither changes the failed acceptance verdict.
The later source-bound network/revoke run passed in 277.73s: 5 measured receipts,
31,799,704 exact bytes, zero holds/unknown/open/violations, full phone/root
cleanup and independent A post-check. Its result is a separate receipt.

Temporary staging remains A-only, two slots, independent 30-minute cleanup;
B services and other issues remain outside scope. Natural sleep and forced Doze
must have separate observations. Forced idle, lack of IPv6, one physical device
or a same-provider host never substitutes for their missing criterion.

The new source-bound full Lifecycle run passed in 659.47s: six required facts,
20 measured receipts and exactly 1,172,946 bytes, with zero reserved bytes,
unknown receipts, open connections or reconciliation violations. Its root/phone
cleanup and independent A post-check passed in the same receipt. The earlier
Lifecycle failure remains unchanged. The first NaturalSleep attempt failed while
Android Play Protect delayed the test APK installation past the 90-second
timeout; its follow-up removed the late-installed APK. The second attempt reached
real sleep and server cutoff, but Flutter failed before post-wake native/egress
assertions. Its 92,831 measured bytes across two receipts reconciled exactly;
cleanup required a separate phone follow-up. Neither failed attempt passes
NaturalSleep or changes the successful Lifecycle scope. A later source-bound
NaturalSleep run passed on one Pixel: 392 seconds without USB, natural deep-idle,
native down after wake, new staging egress/window recovery, 29,662,774 exact bytes
across three receipts, and full phone/A cleanup. See
`evidence/i-028-natural-sleep-staging-20261025T192620Z-286a330b.json`; historical
failed attempts remain preserved and unchanged.

2026-10-26 direct-network positive-control probes: the tablet (Wi-Fi only; no
SIM) and the owner phone on Wi-Fi both had working IPv4 and the fixed UDP DNS
control, but no direct IPv6 was observed. The owner phone was also checked on
cellular with the same result; its original Wi-Fi/mobile-data settings were
restored. Temporary probe packages were removed and the preexisting owner-phone
POC package hash was unchanged. Receipt:
`evidence/i-028-direct-network-baseline-20261026.json`. This does not satisfy
the required positive direct-IPv6 baseline or packet-level pre-NAT capture; the
comprehensive DNS/IPv6 gate remains open. The dual-device acceptance also remains
open; both devices being visible over ADB is only a prerequisite, not a pass.

Follow-up diagnostic probe at 2026-10-26T20:07Z adds the classified IPv6
result: the owner phone has a positive direct IPv6 baseline over Wi-Fi
(`ipv6_failure=none`), while the tablet's fixed IPv6 probe hostname failed
system DNS resolution (`ipv6_failure=dns`). The tablet has no SIM; no cellular
result is attributed to it. The separate fixed UDP DNS control succeeded on
both. Receipt: `evidence/i-028-direct-network-baseline-diagnostic-20261026.json`.
This establishes only a positive-control path on the owner phone; packet-level
pre-NAT capture and included/excluded VPN observations are still required.

The Wi-Fi-only tablet is suitable for the separate simultaneous-device criterion;
it does not need a SIM for distinct keys/windows, cross-owner denial or isolated
revocation. Cellular handover remains outside that criterion. ADB visibility is
only a preflight and does not count as a physical acceptance result.

2026-10-31 follow-up: the Egress diagnostic receipt passed and confirmed settlement plus cleanup, but remains explicitly non-acceptance (`vpn_dataplane_acceptance=false`). The synchronized included AP-station capture saw 8,221 tunnel endpoint packets, zero direct DNS, and five global IPv6 TCP/443 packets. Since the capture is station-filtered rather than UID-attributed, the five packets remain unresolved. The immediate phone-only post-run Probe passed direct IPv4, IPv6, and UDP DNS over Wi-Fi with VPN off. This rules out a persistent post-run IPv6 outage at probe time; it does not close the included UID leak criterion. See `evidence/i-028-egress-diagnostic-20261031T133417Z-bc40107b.json`, `evidence/i-028-packet-audit-included-20261031T1329Z.json`, and `evidence/i-028-probe-after-startup-fix-20261031T134318591Z.json`.

2026-10-31 local source-port attribution improvement is built but not physically verified. Probe-only aggregate counters use DNS `42428`, direct-baseline IPv6 HTTPS `42430`, and included IPv6 HTTPS `42429`; no addresses or packet headers are persisted. This distinguishes only the fixed probe sockets, not all UID traffic. Build and local tests passed; direct-baseline positive-control and included captures on the new APK remain open. See `evidence/i-028-probe-source-port-audit-local-20261031.json` and ADR-064.

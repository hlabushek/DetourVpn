# I-028 acceptance matrix — 2026-10-25

Only I-028 is assigned. Historical failed receipts remain unchanged. A passing
component receipt does not make the full issue or an earlier runner pass.

| Criterion | Verification | Evidence | Current status |
|---|---|---|---|
| Same account/family/device after process restart | Actual different Android PID | `evidence/i-028-main-success.json` | Passed in single successful main run |
| Native handshake, independent UID IPv4/DNS | Fixed probe through A, VPN DNS metadata | `evidence/i-028-main-success.json` | Passed |
| Sustained renewal and actual traffic >185s | Periodic independent egress, generation 7 | `evidence/i-028-main-success.json` | Passed |
| Included/excluded same UID | Compare real direct and staging egress | `evidence/i-028-main-success.json` | Passed |
| Disconnect and remote revoke | Native down, server close and measured settlement | `evidence/i-028-main-success.json` | Passed |
| API+agent failure, restart/reconcile | Native/kernel deadlines, new real connection | `evidence/i-028-main-success.json` | Passed |
| Exact accounting | 13 receipts, 1,810,465 bytes, zero holds/unknown/open/violations | `evidence/i-028-main-success.json` | Passed |
| Corrected timer cleanup | Real A cleanup plus independent post-check | `evidence/i-028-fixed-cleanup-staging.json` | Scoped pass only |
| Background/screen off/forced Doze and wake | Observe native deadline and server gate across each interval, then real recovery | `evidence/i-028-lifecycle-staging-20261024T225819Z-2a9ff221.json` | Passed in one full physical Lifecycle run: three separate 100-second observations, real recovery, phone/A cleanup and independent post-check |
| Natural sleep with USB detached | New unforced deep-idle, zero live A slots/peers, native down after wake and new real staging egress | `evidence/i-028-natural-sleep-staging-20261025T192620Z-286a330b.json` | Passed on one Pixel: 392 seconds without USB, natural deep-idle observed, native down after wake, new staging egress/window recovery; exact settlement and phone/A cleanup passed |
| Wi-Fi/mobile both directions and temporary loss | Positive transport baseline, real traffic after each transition, deadlines/client state | `evidence/i-028-network-revoke-success.json` | Passed in physical run: real Wi-Fi→cellular→loss→Wi-Fi transfer, radio-loss confirmation and valid windows |
| System settings VPN permission revoke | Owner revokes in Android Settings; native down, server settlement, no reconnect | `evidence/i-028-network-revoke-success.json` | Passed in physical run: app-op revoke, native down, server close, zero holds/unknown and 40-second no-restart observation |
| Disconnect during renewal | Delay real accepted renewal response and wait for actual node ACK; disconnect before release; inspect native and server settlement | `evidence/i-028-main-success.json` | Physical pass, zero holds/peers; native stays down |
| Comprehensive DNS/IPv6 leaks | Positive direct IPv6 baseline and scoped pre-NAT observation, included/excluded across transitions/failures | `evidence/i-028-network-revoke-success.json`; ADR-064; separate packet-level receipt required | Direct Wi-Fi IPv6 worked and included-VPN IPv6 was blocked; full leak capture remains open |
| Two physical devices simultaneously | Distinct local keys/windows/counters, cross-owner denial/revoke isolation | Separate physical receipt required | Open. Read-only preflight passed on 2026-10-27: Pixel 8 Pro Android 17 and Wi-Fi-only tablet 25040RP0AG Android 15 are ADB-visible, isolated I-028 packages are absent, Always-on is unconfigured, and source-bound probe APK hashes match. The tablet still needs the probe base APK. No install, staging, tunnel, ownership or cross-revoke behavior was exercised; see `evidence/i-028-two-device-preflight-20261027.json`. |
| Independent provider failure | Approved existing independent infrastructure, explicit manual recovery per architecture | ADR-031/033 and I-019 establish same-provider boundary only | Open; no independent target authorized/identified |
| Single repeatable main run with corrected cleanup | Source-bound Build/Stage/Install/Test and independent cleanup | `evidence/i-028-main-success.json`, `evidence/i-028-main-source-binding.json` | Passed in 474.23s; 13 receipts, 1,810,465 measured bytes, no holds/unknown/open/violations |

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

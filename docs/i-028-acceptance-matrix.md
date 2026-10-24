# I-028 acceptance matrix — 2026-10-24

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
| Background/screen off/natural sleep/forced Doze and wake | Observe native deadline and server gate across sleep, then real recovery | `evidence/i-028-lifecycle-staging.json`, `evidence/i-028-lifecycle-cleanup-followup.json` | 100-second background, screen-off and forced deep-IDLE component observations passed with real recovery; whole run failed on phone USB/ADB loss. Natural sleep untested |
| Wi-Fi/mobile both directions and temporary loss | Positive transport baseline, real traffic after each transition, deadlines/client state | `evidence/i-028-network-revoke-success.json` | Passed in physical run: real Wi-Fi→cellular→loss→Wi-Fi transfer, radio-loss confirmation and valid windows |
| System settings VPN permission revoke | Owner revokes in Android Settings; native down, server settlement, no reconnect | `evidence/i-028-network-revoke-success.json` | Passed in physical run: app-op revoke, native down, server close, zero holds/unknown and 40-second no-restart observation |
| Disconnect during renewal | Delay real accepted renewal response and wait for actual node ACK; disconnect before release; inspect native and server settlement | `evidence/i-028-main-success.json` | Physical pass, zero holds/peers; native stays down |
| Comprehensive DNS/IPv6 leaks | Positive direct IPv6 baseline and scoped traffic observation, included/excluded across transitions/failures | `evidence/i-028-network-revoke-success.json`; separate packet-level observation required | Direct Wi-Fi IPv6 worked and included-VPN IPv6 was blocked; full leak capture remains open |
| Two physical devices simultaneously | Distinct local keys/windows/counters, cross-owner denial/revoke isolation | Separate physical receipt required | Open; only one authorized device currently detected |
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

# Closed beta readiness checkpoint — 2026-11-02

## Decision

**Not ready to invite testers yet.** Several important parts have real owner-only,
hosted, and physical evidence, but the current acceptance documents still list
blocking gates for authentication, operations, and VPN privacy. This checkpoint
does not change any acceptance criteria or authorize a deployment.

This is the free, invite-only beta scope. Payments and public registration are
not prerequisites for that scope. Public or paid release has additional legal,
payment, security-review, supply-chain, support, and reliability gates.

## Current evidence

| Area | Demonstrated | Still open before a closed beta | Evidence boundary |
|---|---|---|---|
| Login and invitation — I-026 | Owner confirms separate-device sign-in and normal UI operation passed. The scope is recorded as owner-reported; device/account identifiers are omitted. | No raw response/correlation evidence is retained. The owner has not yet published the APK to the selected private channel; do not widen this sign-in/UI result into VPN acceptance. | `evidence/i-030-owner-ops-confirmation-20261102.json`; `evidence/i-026-live-miniapp-otp-20261026.json`; `evidence/i-026-beta-live-delivery-receipt-20261026.json` |
| Isolated beta environment — I-030 | Historical receipts cover isolated setup and a bounded owner Android VPN smoke. On 2026-10-29, public HTTPS routes returned Mini App `200`, referrals API `401`, hidden readiness `404`; pinned read-only host checks found API/Caddy and node units active, internal DB/migration readiness `200`, beta node row active with a present runtime and `last_seen` age 0 seconds by DB clock, and zero nonclosed peers. The current Caddy template's related local regression tests passed (`7 passed`), covering route allowlist/order and deny fallback. | This is a point-in-time control-plane/host check, not an external UDP handshake, current user tunnel, privacy test, broad peer compatibility, or recovery drill. The Caddy tests assert source text; they do not simulate Caddy routing. | `evidence/i-030-beta-node-activation-20261026.json`; `evidence/i-030-beta-android-owner-smoke-20261026.json`; `evidence/i-030-beta-ingress-readonly-20261029.json`; `evidence/i-030-beta-runtime-readonly-20261029.json`; `infra/ansible/tests/test_beta_config.py` |
| Backup and restore — I-027 | On 2 November, the registered task was explicitly started successfully, the new encrypted archive authenticated, and that exact archive restored in disposable PostgreSQL 18.6: 42 table manifests matched and two ledger accounts reconciled. The temporary database and cluster were removed. | Natural scheduled execution and missed-trigger catch-up are not demonstrated. Local retention is constrained by the 64 MiB import cap; independent monitoring/alerting is absent. | `evidence/i-027-manual-backup-run-20261102.json`; `evidence/i-027-catchup-followup-20261102.json`; `evidence/i-027-restore-drill-20261102.json`; `docs/issues/I-027.md` |
| Tester Android artifact — ROADMAP phase 6 | The stable `com.detourvpn.client` APK is signed and physically update-installed. JKS restore and both signing passwords passed; owner confirms the separate BitLocker recovery-key file exists. A private Telegram channel and `@DetourVpnHelper` support contact are selected. | Owner publication of the APK remains pending. Recovery-key file contents/access were not inspected; the debug APK is not update-compatible. | `evidence/i-030-beta-signing-restore-20261101.json`; `evidence/i-030-owner-ops-confirmation-20261102.json`; `evidence/i-026-beta-session-20261101.json`; `docs/adr/066-android-beta-apk-signing.md`; `docs/runbooks/beta-tester-quickstart.md` |
| Android VPN and privacy — I-028 | Main, lifecycle, natural-sleep, network/revoke, two-device, scoped included-IPv6 block, independent-provider recovery, and ADR-070 established-session status have bounded physical receipts. The I-019 drill observed fresh A/B egress, exact settlement and cleanup; distinct origin ASNs were observed. | The broad packet audit was deferred by ADR-072, not passed. Evidence does not prove device-wide leak freedom, full upstream/provider-chain independence, or automatic failover. | `i-028-acceptance-matrix.md`; `evidence/i-028-two-device-staging-20261029T101845Z-b0085c2a.json`; `evidence/i-028-egress-diagnostic-20261031T165654Z-85a5fc2c.json`; `evidence/i-028-egress-diagnostic-20261031T182523Z-91d07c8e.json`; `evidence/i-028-ipv6-included-block-20261031T182345Z.json`; `evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json`; ADR-070; ADR-071; ADR-072; ADR-073 |
| Android Always-on/Lockdown — I-029 | Local implementation and bounded routing tests exist; five synthetic headless-start negative tests now cover missing/expired session, invalid origin, unavailable saved node, and old-lease closure before permission denial. The release manifest opts out of Always-on support. | Physical restart/Lockdown/recovery acceptance is open. Do not promise Always-on or kill-switch behavior. Confirm the exact build distributed to testers does not imply those guarantees. | `docs/issues/I-029-android-safety-routing.md`; `docs/verification-i-029.md`; `docs/adr/042-android-always-on-lease-bootstrap.md` |
| Tester operations — ROADMAP phase 6 | Support contact `@DetourVpnHelper` and private-channel delivery route are selected; tester instructions and support runbook now name the contact. I-027's new-archive restore drill passed. | APK posting is pending owner action. Independent use of the instructions, load/usage-lag measurements, memory/battery baselines, node-rotation incident drill, measured basic SLOs, free-traffic cost, and a complete current critical-vulnerability assessment remain open. Phase 6 is not closed. | `ROADMAP.md`, phase 6 DoD; `docs/runbooks/beta-tester-quickstart.md`; `docs/runbooks/beta-tester-support.md`; `docs/issues/I-027.md`; `apps/client/lib/diagnostic_report.dart`; `docs/adr/067-consent-based-diagnostic-sharing.md` |

## Public or paid release gates

These are separate from the free closed-beta minimum and remain open where the
roadmap has not recorded acceptance: legal review, payment provider and refunds,
independent security review, SBOM/dependency review, account deletion/support,
public SLOs, independent-provider reliability, and public privacy/compliance
materials. Do not interpret a closed-beta decision as public-release approval.

On 2026-10-29 a Python-only audit of the locked runtime and development groups
found no known vulnerabilities after updating three development transitive
packages in `uv.lock` (`certifi`, `filelock`, `virtualenv`). The earlier local
development environment scan had reported 26 vulnerability records for those
packages. This does not cover Flutter/Android, Node, OS/container packages,
licenses, an SBOM, or an independent security review. Receipt:
`evidence/i-030-python-dependency-audit-20261029.json`.

The same day's `npm audit --package-lock-only` found no known advisories in the
Telegram Mini App and bot lockfiles; Flutter `pub get --enforce-lockfile`
completed without advisory messages and left `pubspec.lock` unchanged. These
checks do not cover Gradle/Android artifacts, SDK/NDK, system packages, complete
license/SBOM review, or independent assessment. Receipt:
`evidence/i-030-node-flutter-dependency-check-20261029.json`.

An OSV query of all four Gradle lockfiles found 87 advisory matches across 18
coordinate/version pairs. They are in internal test-platform or debug/test
configurations; the app's `releaseRuntimeClasspath` query covered 49 coordinates
and returned no matches. Keep the test/build-tool findings visible for the
broader supply-chain review; this is not a claim that the Gradle build toolchain
or all Android artifacts are vulnerability-free. Receipt:
`evidence/i-030-gradle-dependency-audit-20261029.json`.

On 2026-11-02, the locked Python runtime and all-groups exports again had no
known pip-audit findings; both Node lockfiles had zero npm advisories; OSV found
no matches in 27 hosted Pub packages. The current four Gradle lockfiles are
unchanged: OSV reports 87 advisory-match records across 18 coordinate versions
outside the beta app's 49 release-runtime coordinates. OSV supplied no severity
metadata for those 87 records. The audit does not cover Gradle build tooling,
Android SDK/NDK, OS/container packages, native Go dependencies, complete SBOM or
license review, or independent assessment. It narrows but does not close the
critical-vulnerability gate. Receipt:
`evidence/i-030-dependency-audit-20261102.json`.

## I-028 packet-audit options

## I-028 packet-audit options

| Route | Current evidence | Assessment |
|---|---|---|
| Pixel directly on MGTS Wi-Fi | The 2026-10-29 phone-only probe confirmed direct IPv6 and fixed UDP DNS with VPN off. No traffic was observed before the router's NAT. | Useful positive baseline only; it does not satisfy ADR-064's observation requirement. |
| Windows Mobile Hotspot + PktMon | Microsoft documents counters-only collection without packet logging. Filters can select IP, transport, and port, but an IP/port filter does not distinguish source from destination; counters are reported by component and Tx/Rx. The recorded Pixel hotspot check had no global IPv6 address/default route. A fresh read-only `pktmon list --json` from the current non-elevated shell returned access denied (exit code 5); no capture started. | This may support bounded IPv4/DNS aggregate observations if a hotspot-side component and filter attribution are established. Separate filtered windows would be needed to keep categories distinguishable; this is not validated locally. The previously successful IPv6 baseline was on MGTS Wi-Fi, not the hotspot, so it cannot supply ADR-064's direct baseline for a hotspot capture. PktMon does not currently provide a complete path for the required DNS/IPv6 audit. Do not ask the owner to elevate solely to map an adapter unless the hotspot first has a working direct IPv6 baseline. [PktMon syntax](https://learn.microsoft.com/en-us/windows-server/networking/technologies/pktmon/pktmon-syntax), [PktMon filters](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pktmon-filter-add), [PktMon counters](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/pktmon-counters), [Windows Internet Sharing](https://learn.microsoft.com/en-us/windows-hardware/drivers/mobilebroadband/creating-and-configuring-internet-sharing-experiences). |
| Linux laptop as controlled AP + tcpdump | The user confirmed that the laptop's Wi-Fi adapter supports AP mode separately and tcpdump is available; the earlier `iw list` query produced no interface-combinations block, and no LAN cable is available. Google documents Wi-Fi sharing on Pixel 3 and later; Android tethering supports IPv4/IPv6 dual stack and IPv6 SLAAC. | Candidate no-LAN route: keep Pixel connected to MGTS Wi-Fi, tether its Wi-Fi upstream to the Linux laptop over USB, and use the laptop Wi-Fi as the controlled AP for the tablet. This avoids relying on concurrent client/AP support on one Wi-Fi adapter. It is an inference from platform support, not a verified route: global IPv6 must reach the tablet through both tethering and AP sharing, and a USB data cable must be available. First test only that upstream IPv6 path; no AP configuration or packet capture yet. [Pixel tethering](https://support.google.com/pixelphone/answer/2812516), [Android tethering IPv6](https://source.android.com/docs/core/ota/modular-system/tethering). |
| Defer the physical audit | No new packet run or network change was made. | I-028 remains open. The issue matrix and current beta runbook still make privacy acceptance a pre-invite gate. Deferring the run does not waive it; changing that beta scope requires an explicit owner decision and, if acceptance changes, a recorded ADR/update. |

The smallest evidence-producing next step is to confirm whether USB tethering
from the Pixel while it is connected to MGTS gives the Linux laptop an IPv6
default route. If that passes, the Linux Wi-Fi adapter can host the tablet as a
separate AP and tcpdump can observe it before the laptop's forwarding/NAT. This
route still requires a later direct baseline on the tablet and phase-by-phase
bounded packet audit. An elevated PktMon listing alone would not meet the
requirement. Repeating phone-only probes or staging scenarios will not answer
the pre-NAT question.

## Ordering for the next work

1. Keep I-026 limited to its recorded owner-only acceptance; friend onboarding
   remains unverified. The 2026-10-29 beta-host check is only a point-in-time
   status snapshot; it does not accept a user tunnel or friend onboarding.
2. Select one issue and close its real acceptance gap. Do not rerun physical
   I-028 scenarios already accepted. The open packet audit still needs a
   controlled pre-NAT observation point; the Linux adapter did not report a
   valid interface-combinations block, and no LAN cable is currently available.
3. Keep beta-only operational and security limitations explicit. Payment work
   stays out of scope for a free invited group; Always-on/Lockdown and kill
   switch claims stay disabled until physically accepted.

## Limitations of this snapshot

This snapshot includes a fresh HTTPS route check and read-only API/database/node
status inspection. It is not an external UDP or user-tunnel test, a Telegram
delivery/invite check, or a Windows backup-task check. Hosted receipts are
time-bound. A successful local build, mock, handshake, or single owner smoke is
not release acceptance.

## Follow-up — 2026-11-01

The owner selected `com.detourvpn.client`, created a local signing key outside
the checkout, and built a signed `0.1.0-beta.1` release APK. Metadata and
signature verification passed; the sanitized receipt is
`evidence/i-030-beta-apk-20261101T172209Z.json`. At that checkpoint, signing-key
copy verification, physical update installation, and distribution were still
open. No tester invite was granted. The owner-scoped I-028 evidence includes a
passed physical ADR-070 established-session status check. I-026 friend
onboarding and a natural I-027 scheduled-backup run remain open. The bounded
I-028 independent-provider criterion passed in
`evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json`; the broad packet
audit was deferred by ADR-072, not passed. The receipt does not support a
device-wide leak-free claim or prove full upstream independence.

The initial 1 November I-027 scheduled retry failed before transfer because the
task process could not see the local backup-key directory. A manual encrypted
pull from the Codex shell passed, but did not count as scheduled acceptance;
see `evidence/i-027-scheduled-retry-20261101.json`. At the time of the first
keystore check, no approved off-device destination was visible to the execution
shell; see `evidence/i-030-keystore-backup-check-20261101.json`. Later results
and the superseding status are recorded below.

The owner later confirmed that `backup.key` was absent from ordinary Windows
PowerShell, while the Codex execution shell saw a file at the same path. The
owner then ran the ADR-077 helper: the explicit daily-task start passed and a
new local encrypted archive was verified. A natural daily trigger has not yet
been observed; Task Scheduler reports the next run at 2026-11-02 07:00 local.
The signing JKS backup was subsequently copied to the owner-selected
BitLocker-protected `E:` removable volume; the owner-run helper confirmed the
copy SHA-256 matches. Restore from that copy and verification of the JKS and
BitLocker recovery passwords in the owner's password manager remain open. The
current local APK SHA-256 matches its sanitized receipt. Physical update
installation and private distribution remain unverified.

The source-bound Egress receipt `evidence/i-028-egress-diagnostic-20261031T165654Z-85a5fc2c.json`
contains 25 hold observations and a true stale-handshake/inbound-progress
status fact, with native tunnel, network and lease valid. This satisfies the
physical ADR-070 observation for that bounded build; older text in this
checkpoint calling ADR-070 open is superseded. ADR-072 still defers the broad
packet audit and does not establish device-wide leak freedom. The beta remains
not ready for tester invitations: friend onboarding, the natural scheduled
backup trigger, signing-key restore/password recovery, physical update install,
and tester operations/support acceptance are still open.

## Superseding owner follow-up — 2026-11-02

The owner has now signed in on Pixel 8 Pro, installed/launched the signed beta
APK, connected and disconnected the tunnel, and completed one controlled IPv4
ping plus one hostname-resolution/IPv4 ping while ADB observed the VPN network
agent. These are limited owner smoke results; they do not prove DNS privacy,
exit IP/location, IPv6 behavior, server-side revoke acknowledgement, or friend
onboarding. ADB readback of the installed APK matched the latest signed build
receipt by SHA-256, confirming the exact owner artifact. It does not verify
distribution to another tester. Current owner quota is below the 1 MiB minimum
for another window.

The daily backup task completed at 15:04 local with result 0 and imported a new
Fernet-authenticated archive. This was an explicit task start, not a natural
07:00 trigger. A disposable past-trigger `StartWhenAvailable` task remained
Ready and was removed without running; catch-up remains unproven. The new
archive was restored in a disposable local PostgreSQL 18.6 cluster: all 42 table
manifests matched and two ledger accounts reconciled. The archive hash remained
unchanged, and the temporary database and cluster were removed. See
`evidence/i-027-catchup-followup-20261102.json` and
`evidence/i-027-restore-drill-20261102.json`.

The owner-run signing-key restore from the encrypted removable backup produced
an APK with the prior signer, and both JKS passwords were accepted. The
BitLocker recovery-key file on the PC remains owner-reported and unverified;
its contents were not inspected. See
`evidence/i-030-beta-signing-restore-20261101.json`.

Phase 6 remains open. The current runbooks are drafts and have not been used by
a friend tester. No measured load/usage lag, memory/battery baseline,
node-rotation incident drill, basic SLO, free-traffic cost, or complete current
critical-vulnerability review is recorded. The owner has since confirmed
separate-device sign-in/UI success and selected `@DetourVpnHelper` plus a private
Telegram channel; APK publication is pending the owner's manual upload. These
updates close the corresponding owner-confirmed onboarding/contact/file-presence
checks only. They do not accept VPN traffic or authorize a public invite.

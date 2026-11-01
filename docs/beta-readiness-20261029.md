# Closed beta readiness checkpoint — 2026-10-29

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
| Login and invitation — I-026 | The issue records owner-only Mini App → Android OTP sign-in as accepted. Receipts also record owner-observed replay/duplicate rejection and aggregate-only backend corroboration. | No friend onboarding is accepted. The receipts do not contain raw response/correlation evidence; do not widen the accepted scope beyond the owner-only flow. | `evidence/i-026-live-miniapp-otp-20261026.json`; `evidence/i-026-beta-live-delivery-receipt-20261026.json` |
| Isolated beta environment — I-030 | Historical receipts cover isolated setup and a bounded owner Android VPN smoke. On 2026-10-29, public HTTPS routes returned Mini App `200`, referrals API `401`, hidden readiness `404`; pinned read-only host checks found API/Caddy and node units active, internal DB/migration readiness `200`, beta node row active with a present runtime and `last_seen` age 0 seconds by DB clock, and zero nonclosed peers. The current Caddy template's related local regression tests passed (`7 passed`), covering route allowlist/order and deny fallback. | This is a point-in-time control-plane/host check, not an external UDP handshake, current user tunnel, privacy test, broad peer compatibility, or recovery drill. The Caddy tests assert source text; they do not simulate Caddy routing. | `evidence/i-030-beta-node-activation-20261026.json`; `evidence/i-030-beta-android-owner-smoke-20261026.json`; `evidence/i-030-beta-ingress-readonly-20261029.json`; `evidence/i-030-beta-runtime-readonly-20261029.json`; `infra/ansible/tests/test_beta_config.py` |
| Backup and restore — I-027 | On 1 November, the owner-profile recovery helper authenticated the existing encrypted archive, installed the existing key, explicitly started the registered daily task (result 0), and verified a new local encrypted archive. | The natural daily trigger and a database restore from the newly imported archive have not been observed. Retention and independent monitoring remain limited or deferred. | `evidence/i-027-beta-schedule-followup-20261029.json`; `evidence/i-027-beta-task-principal-20261029.json`; `evidence/i-027-scheduled-retry-20261101.json`; `evidence/i-027-key-recovery-20261101.json`; `docs/adr/077-i027-existing-key-recovery.md` |
| Tester Android artifact — ROADMAP phase 6 | The owner selected stable application ID `com.detourvpn.client` and built signed release `0.1.0-beta.1`; package metadata and APK signature verification passed, with a sanitized receipt. Its local SHA-256 still matches the receipt. Release source disables Always-on, backup, and cleartext traffic. | A matching copy of the signing keystore is verified on the BitLocker-protected removable volume, but restoring from it and verifying password recovery remain open. Physical update installation over the same signed package and private tester distribution remain unverified; the debug APK is not update-compatible. | `evidence/i-030-beta-apk-20261101T172209Z.json`; `evidence/i-030-keystore-backup-check-20261101.json`; `docs/adr/066-android-beta-apk-signing.md`; `docs/runbooks/flutter-client.md`; `scripts/verify_beta_apk.py` |
| Android VPN and privacy — I-028 | Main, lifecycle, natural-sleep, network/revoke, two-device, scoped included-IPv6 block, independent-provider recovery, and ADR-070 established-session status have bounded physical receipts. The I-019 drill observed fresh A/B egress, exact settlement and cleanup; distinct origin ASNs were observed. | The broad packet audit was deferred by ADR-072, not passed. Evidence does not prove device-wide leak freedom, full upstream/provider-chain independence, or automatic failover. | `i-028-acceptance-matrix.md`; `evidence/i-028-two-device-staging-20261029T101845Z-b0085c2a.json`; `evidence/i-028-egress-diagnostic-20261031T165654Z-85a5fc2c.json`; `evidence/i-028-egress-diagnostic-20261031T182523Z-91d07c8e.json`; `evidence/i-028-ipv6-included-block-20261031T182345Z.json`; `evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json`; ADR-070; ADR-071; ADR-072; ADR-073 |
| Android Always-on/Lockdown — I-029 | Local implementation and bounded routing tests exist; five synthetic headless-start negative tests now cover missing/expired session, invalid origin, unavailable saved node, and old-lease closure before permission denial. The release manifest opts out of Always-on support. | Physical restart/Lockdown/recovery acceptance is open. Do not promise Always-on or kill-switch behavior. Confirm the exact build distributed to testers does not imply those guarantees. | `docs/issues/I-029-android-safety-routing.md`; `docs/verification-i-029.md`; `docs/adr/042-android-always-on-lease-bootstrap.md` |
| Tester operations — ROADMAP phase 6 | The client now has a local opt-in error-report preview and Android Sharesheet path. Flutter tests verify the report allowlist and require explicit confirmation before invoking the share channel. | The share flow has no designated support recipient and has no physical-device acceptance yet; selecting an external app can transmit the text. Automatic crash reporting is not implemented. No acceptance evidence for tested user instructions, load and usage-lag measurements, memory/battery baselines, incident runbooks/node rotation, measured basic SLOs, or free-traffic cost. Phase 6 DoD also requires reproducible incidents, verified recovery, and no critical vulnerabilities. Search of the Android client source found no support URL, `mailto:` route, Sentry, or Crashlytics integration. | `ROADMAP.md`, phase 6 DoD; `apps/client/lib/diagnostic_report.dart`; `apps/client/lib/connection_screen.dart`; `docs/adr/067-consent-based-diagnostic-sharing.md`; `docs/verification-telegram-bot.md`; `docs/runbooks/beta-environment.md` |

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

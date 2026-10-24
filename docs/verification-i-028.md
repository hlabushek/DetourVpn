# I-028 physical scenario and corrected cleanup — 2026-10-24

## Current continuation: main and network/revoke runs passed separately

The first separate lifecycle runner is preserved at
`docs/evidence/i-028-lifecycle-staging.json` with `passed=false`. It recorded
real 100-second background, screen-off and forced deep-IDLE intervals, valid
server windows and new real transfers after wake. The phone then disappeared
from ADB after the first positive cellular tunnel probe; automatic phone
restoration failed, so the complete lifecycle scope did not pass. Exact USB
loss cause is undetermined. A's root cleanup/timer/independent remote post-check
passed and all 2,606,215 measured bytes across 19 receipts reconciled with no
holds, unknown receipts or open connections. The separate
`docs/evidence/i-028-lifecycle-cleanup-followup.json` documents eventual phone
restoration; it cannot retroactively change the failed runner. Phone-only radio
and ADB-reverse continuity tests passed with both radios temporarily off.
A shorter source-bound network/Settings revoke retry is prepared separately.
Its first real run failed at the temporary-loss assertion: Android kept the
saved `mobile_data` preference at 1 after `svc data disable`, while telephony
reported state 0, Wi-Fi was disabled and no IPv4 route existed. Preserve that
failure as `docs/evidence/i-028-network-revoke-radio-state-failure.json`. It
also recorded a positive direct IPv6 baseline with blocked included-VPN IPv6,
real cellular staging egress, full root/phone cleanup and exact settlement of
136,624 measured bytes with no holds/open/unknown receipts. Phone-only checks
demonstrated ADB and ADB reverse continuity with both radios off. The corrected
test reads actual Wi-Fi and telephony state and rejects missing/mixed states.

A second network run (`docs/evidence/i-028-network-revoke-settings-timeout.json`)
recorded actual Wi-Fi/mobile/loss/Wi-Fi transfer, positive direct IPv6 versus
blocked included-VPN IPv6 and the owner's real Settings permission revoke. It
timed out before completing the controller close/settlement assertion: one
unknown receipt and 16 MiB remained reserved at failure time. Root/phone cleanup
and independent A post-check passed, but this receipt remains `passed=false`.
The test was then changed to foreground its own app after observing revoke and
bound the close/no-restart waits. Its later source-bound physical run passed:
`docs/evidence/i-028-network-revoke-success.json`, pytest **1 passed in
277.73s**. It observed valid real transfers across Wi-Fi/mobile/loss/Wi-Fi,
system app-op revoke with native down/server close and 40 seconds without
self-restart. All 31,799,704 measured bytes from 5 receipts reconciled with
zero reserved bytes, unknown receipts, open connections or violations. Phone and
A cleanup, timer disarm, temporary grant removal and independent post-check
passed. The earlier failures remain unchanged. A direct working IPv6 baseline
and inside-VPN IPv6 denial are proven, but packet-level DNS/IPv6 leak capture
remains open.

The current operator completed one full main physical run successfully:
`docs/evidence/i-028-main-success.json`, pytest **1 passed in 474.23s**.
All main facts passed, including real node ACK before Disconnect during a delayed
renewal response, native staying down after release, idempotent Disconnect,
server gates/peers closed and zero holds. Sustained transfer reached generation 7.
Exactly 1,810,465 measured bytes across 13 receipts reconciled, with zero reserved
bytes, unknown receipts, open connections or violations. All cleanup components,
timer disarm, removed grant and independent unprivileged post-check passed.
`i-028-main-source-binding.json` preserves the launcher-validated inputs used by
this run; later lifecycle source changes are separate. Its static `open_cases`
still lists the renewal race, but the explicit race facts and ACK/closure
observations passed; the new runner removes covered cases in future receipts.
Historical failure receipts remain unchanged. This successful main scope does
not close I-028: separate lifecycle, natural sleep, leak, two-device and provider
requirements are tracked in `docs/i-028-acceptance-matrix.md`.

Local continuation checks: Ruff/format, Windows/Linux-target mypy, 72 v3 tests on
isolated real PostgreSQL, 15 operator/diagnostic tests, one real WSL/Windows pipe
drill, Flutter analyze, 70 Flutter tests and all five integration entrypoint/native
probe builds passed. The final foreground/bounded-wait change passed Ruff,
format, mypy, Dart analyze and a complete Build before the successful physical
NetworkRevoke run.

The sections below retain the historical sequence and its evidence boundaries.

Final continuation result: the automated physical scenario facts below passed.
The corrected operator's separate actual staging preparation/cleanup also passed
with `cleanup_complete=true`, `timeout_timer_disarmed=true`, zero gate-close
errors, preserved journal and removed grant. An independent unprivileged check
confirmed inactive services/timer, absent own namespaces/links and UDP/reverse
listeners. See `docs/evidence/i-028-fixed-cleanup-staging.json`, bound to runtime
SHA-256 `1898bb981c09b64f546a7b35635493d113e19f75c6f552fa10e0254552e24094`.
That check used no phone and no traffic; it does not retroactively turn the
earlier full runner's false result into a pass. The physical receipt and fixed
cleanup receipt have separate scopes. The phone can be disconnected. Full I-028
remains open for natural sleep/Doze, network transition, system permission
revoke, in-flight Disconnect, two physical devices, independent providers and
comprehensive DNS leak capture. IPv6 blocking contrast was not demonstrated
because the direct baseline had no IPv6.

Final local checks: Ruff/format and Linux/Windows-target mypy passed; operator
and diagnostic tests 12 passed, 2 POSIX-only Windows skips; real namespace lab
passed; Flutter analyze, 70 unit tests, seed/run/cleanup and native probe builds
passed. Production/B services were not modified.

Latest run: all nine recorded physical facts passed, including actual IPv4/DNS,
same UID included/excluded routing, >185 seconds of periodic real transfer,
acknowledged generation 7, Disconnect, remote revoke, API+agent loss with native
and kernel expiry, and agent restart followed by a new real tunnel. The ledger
settled exactly 4,189,203 measured bytes across 11 receipts, no reservations,
unknown receipts, open connections or reconciliation violations. See
`docs/evidence/i-028-android-staging-timer-failure.json` (immutable run copy).

The overall run remains failed solely because `root_stop.timeout_timer_disarmed`
was false. All other component cleanup steps and independent remote post-check
passed. Root cause: the command wrapper discards stdout on nonzero exit;
`systemctl is-active` reports `inactive` with exit 3. Fresh read-only
`systemctl show --property=ActiveState --value` confirmed `inactive` with exit 0.
The operator now queries that property, refuses empty/active/error states, and
keeps timer/service separation. Four regression cases and the isolated Linux
lab passed. Actual remote cleanup later passed in the separate receipt above.
Phone testing for this scenario is finished; the remaining cleanup check needs
no phone. This does not close the full issue's unexecuted matrix.

Earlier status: a subsequent real run confirmed independent-UID staging IPv4
egress, fixed UDP DNS, configured VPN DNS and a fresh native handshake. It then
failed during `sustained_renewal`, whose compound native assertion included a
handshake no older than 60 seconds throughout idle waiting. The failing field
was not retained, so handshake age is a hypothesis, not a demonstrated cause.
Three measured receipts settled exactly:
12,442,291 used bytes, zero reservations, unknown receipts or open connections.
The overall cleanup flag was false; a read-only follow-up found all temporary
services/timer inactive, sudo grant absent, phone session/test APK/reverse absent,
app-op restored and no local API listener. Preserve the failure and follow-up at
`docs/evidence/i-028-android-staging-renewal-failure.json` and
`docs/evidence/i-028-cleanup-followup.json`; the original cleanup failure cause
was not retained. A revised test separates handshake freshness from window
validity, periodically requires real egress and records bounded native state;
cleanup now reports component outcomes. **I-028 is not complete.**

Historical status: the first real bounded staging/Android run **failed at
`included_probe`**. Actual process restart retained the same real local account,
family and device. The independent UID saw an Android VPN and configured VPN DNS,
but did not confirm staging IPv4 egress or a DNS reply. The failed physical receipt
is preserved unchanged at `docs/evidence/i-028-android-staging.json`.
Temporary staging/transport/phone cleanup and an independent remote post-check
passed. B was not changed. **I-028 is not complete.**

The initial preparation below preceded that run. Server time synchronization was
then fixed using verified reachable Cloudflare NTP through a runtime link setting;
the root installer/real agent/API transport and two slots were exercised. The
original NTP servers timed out from A, independently of the PC VPN.

## Offline continuation after the failed physical run

- The new probe now waits for `Application.onCreate` to finish, matching the
  established POC runners. Previously it raced initialization. This is a plausible
  test startup defect. The subsequent physical baseline passed, but this does
  not isolate the cause of the earlier full tunnel failure.
  It records initialization/active-network state and fixed DNS/TLS/permission/route
  failure categories; arbitrary errors, addresses and payloads are not emitted.
- The runner accepts only explicit diagnostic fields and exact boolean/category
  values. It no longer asserts on a `CompletedProcess` object, whose repr could
  expose captured output. A fresh-handshake fact is recorded independently of
  egress. Node aggregate transfer counters and real ledger/receipt/reservation
  state are retained after revoke even on an early failure. No accounting outcome
  is retroactively inferred for the previous disposable database.
- The local namespace lab now passes real UDP DNS packets through the pinned
  AWG cores and both NAT boundaries. A **synthetic local DNS responder** runs on
  the fake WAN; it establishes packet forwarding in that scope, not internet DNS
  or Android behavior. Closed gates and independent kernel expiry block UDP too.
- Root cleanup now disarms its timer after resources close, including retry with
  an old cleanup marker. It never stops its own timeout service. Supervisor/timer
  behavior in the WSL lab remains explicitly simulated.
- `archive_closed.py` permits recoverable archive only after a root-owned cleanup
  receipt, absent runtime keys/grant/config, inactive services, no own namespaces,
  interfaces/rules/listeners, and a disarmed timer. It preserves the private journal
  under root-private `/opt/detourvpn-i028-archives/closed-*`. No recursive deletion
  of old releases is performed. The real Linux filesystem test exercises the
  `/run` tmpfs copy boundary and rejects false/symlinked receipts, secret symlinks,
  active services and non-private destinations. Server archival is not yet run.

Final checks for this continuation: Ruff/format and platform-specific mypy passed;
diagnostics/operator/real loopback tests **9 passed, 2 Windows skips** (POSIX guards
also exercised in the Linux lab); the expanded namespace lab passed; Flutter
analyze and **70 unit tests** passed; seed/run/cleanup APKs and native probe APK
built. No phone or privileged VPS changes were made during those offline checks.
The local receipts and compiled inputs are bound to the current source hashes.

The subsequent phone-only probe passed at 2026-10-24T12:34:17Z: application
initialization and target UID matched, direct IPv4 HTTPS and fixed UDP DNS
succeeded, and the actual active network was validated with no VPN. Its test APK
was removed. See `docs/evidence/i-028-probe-after-startup-fix.json`.
This is a direct baseline, not tunnel acceptance. A bounded server restart is
prepared and staged; privileged archival/install is awaiting local owner sudo.
Natural sleep/Doze, network switching, system revoke,
late renewal races and other unexecuted cases remain open.

## Physical preflight continuation

Historical preflight before the failed run:

The owner reconnected the Pixel on 2026-10-24. Native instrumentation initially
failed because AGP overwrote the client test manifest's extra instrumentation
with its configured runner. The probe was moved into the existing native POC
test APK with an explicit opt-in runner; its UID matches the POC target and
INTERNET is declared. This is test packaging only; the POC app/data are preserved.
The probe now executes on the physical phone. Both the original APK declaration
failure and its correction were detected before enabling staging resources.

Sota Connect was initially active. The owner disabled it; an actual active-network
probe confirms no VPN. The system's dumped VPN capabilities remain populated
after disconnect and were a false preflight indicator; this stale check was
removed. Direct HTTPS IP checks currently fail with a sanitized DNS category,
including the fixed AWS fallback. The fixed UDP DNS question intermittently
succeeds on Wi-Fi. A bounded mobile-network baseline attempt was also made and
the original Wi-Fi setting restored. No direct egress contrast is accepted.
See `docs/evidence/i-028-probe-baseline.json`; the full matrix remains open.

The first privileged installer attempt did not create its release/helper/slots:
the host reported `NTPSynchronized=no`, despite an active timesyncd and a prior
small reported offset. Read-only checks confirmed those paths absent. The
installer now emits a fixed line-number error, can restart only the already
active timesyncd service, and waits for its clock prerequisite without changing
timezone/NTP configuration or bypassing the gate. The owner entered sudo only
in the local SSH terminal; subsequent readiness is still being checked.

## Changes

- `infra/staging-v3/`: fixed two-slot operator/installer, restricted localhost TLS
  relay, scoped sudo lifecycle, independent 30-minute cleanup and retained private
  aggregate journal. Root runtime had already passed the local namespace lab;
  this continuation binds its runtime dependencies to receipt hashes and checks
  both native artifact pins before exercising them.
- `scripts/i028_pipe_relay.py`: necessary WSL NAT transport to the Windows API's
  loopback, over subprocess pipes and only three internal node paths. Actual local
  WSL connection to Windows 127.0.0.1 was refused before this transport was added.
- `scripts/run_i028_staging.ps1`: complete Build/Stage/Install/Status/Test/Finish
  workflow. Builds before exposure, checks snapshot and current source bindings,
  keeps owner password entry in a visible local SSH terminal.
- `apps/client/integration_test/i028_live_test.dart` and
  `services/api/tests/test_i028_live.py`: seed/run/cleanup phases, actual PID/account
  restore, real controller/native/node paths, asynchronous independent UID probes,
  sustained renewal/split/revoke/outage/recovery, exact ledger assertions and
  cleanup. These compiled scenarios are not yet physical acceptance evidence.
- Android test-only `I028Probe` and instrumentation manifest: fixed IPv4/IPv6/DNS
  requests under the existing POC UID. No arbitrary destination or raw direct-IP
  output. Probe APK built; its cross-target instrumentation has not run on device.
- `apps/client/test/window_connection_test.dart`: fake native connect now yields
  20 ms so later ACKs have a later real monotonic timestamp. The prior instantaneous
  fake intermittently produced the same millisecond deadline and failed the lost
  renewal response test. Production deadline enforcement was not weakened.
- ADR-041, issue, roadmap and runbook now record the authorized temporary scope,
  owner actions, transport, cleanup and remaining acceptance boundaries.

## Historical preparation checks

| Check | Result |
|---|---|
| Ruff check and format for changed Python/operator files | Passed |
| mypy Linux operator/lab and Windows transport/harness | Passed, two platform-specific invocations |
| Isolated WSL net/mount lab | Passed: real pinned AWG, two closed slots, verified TLS, NAT transfer, private/other-slot rejection, kernel expiry, no-follow/immutable configuration, idempotent cleanup |
| Lab supervision boundary | systemd lifecycle/timer scheduling simulated; no remote timer acceptance |
| Operator negatives + real WSL/Windows pipe drill | 4 passed, 2 POSIX-only tests skipped on Windows; real no-follow checks passed in Linux lab |
| Flutter analyze | Passed, no issues in final build |
| Flutter unit tests | 70 passed after deterministic fake timing correction |
| Android debug integration builds | seed, run, cleanup compiled successfully in reviewed ASCII snapshot |
| Independent UID instrumentation APK | Gradle BUILD SUCCESSFUL |
| Remote source allowlist | Copied; SHA-256 and bash -n passed, including final no-bin installer update |
| Phone detection at end of preparation | No authorized device connected |

The build emits the existing NDK-version recommendation; all requested debug
builds succeeded. No production NDK/toolchain change was made during preparation.
No new application library or database/API contract was introduced here. The
ordinary repo APKs were preserved while test targets compiled in the snapshot.

Sanitized local receipts:
`docs/evidence/i-028-bootstrap-local.json`,
`docs/evidence/i-028-loopback-transport.json`.
The ignored `.cache/i028-ready.json` binds the physical runner build to current
inputs. Raw build output is local under `%LOCALAPPDATA%/ProjectVpn`; live runner
captures raw device/API/SSH results rather than echoing credentials.

## Remaining work and risks

First connect/unlock/authorize the physical phone, then start the visible owner
sudo terminal only when ready to immediately run the bounded Test action. The
server needs the owner's staging sudo password locally; it is not available to
the agent and must never enter chat. No approval is being inferred from a timer.

The first physical slice will check sustained renewal, real-account process
restore, included/excluded UID egress, remote/explicit disconnect, combined
API/agent loss, independent deadlines, restart, and exact accounting. Its failure
must remain a failure with cleanup evidence. Natural sleep/forced Doze,
Wi-Fi/mobile transition, system permission revoke, Disconnect versus in-flight
renewal, comprehensive DNS leak capture, simultaneous physical devices and
provider resilience remain open separately. IPv6 denial only proves a routed
contrast when the direct baseline itself has IPv6.

Principal security risks are root firewall/namespace isolation, late grants,
temporary secrets, incomplete remote/process cleanup and phone policy restoration.
The operator changes only dedicated rules/resources, denies slot forwarding before
waiting for shutdown, uses kernel deadlines and an independent host timer. These
controls require the pending remote run; a local successful lab cannot certify
the host's systemd/firewall/network behavior. Public release, SBOM and production
operations are not accepted. Continue I-028; do not automatically start another issue.

Commands and manual actions: `docs/runbooks/v3-android-staging.md`.

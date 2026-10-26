# I-028: temporary v3 staging A and physical Android

The current criterion-to-evidence table is `docs/i-028-acceptance-matrix.md`.
Keep historical failure receipts unchanged before any new Test/Probe invocation.
The continuation adds a bounded delayed real renewal response/ACK race to Test;
it does not enable the separate lifecycle/network/provider matrix automatically.

`Test -Suite Main` is the main scenario (the default). After its complete cleanup,
build the current sources, Stage/Install with `-RestartClosed`, then use
`Test -Suite Lifecycle` for the separately reported physical lifecycle matrix.
Build compiles seed/run/lifecycle/network_revoke/natural_sleep/cleanup, and input
binding includes the device action module. `Test -Suite NaturalSleep` writes
`i-028-natural-sleep-staging.json`; it has its own fresh Stage/Install window.
Lifecycle records `i-028-lifecycle-staging.json` and requires an
awake/unlocked physical phone, Wi-Fi/mobile switches enabled and working data.
If a receipt already exists, the runner writes a new UTC-and-random-suffixed
JSON filename rather than replacing historical evidence. Inspect the newest
receipt and retain every failed attempt unchanged. Android Play Protect may
pause probe APK installation; the owner must confirm the expected test APK on
the phone within the runner's 90-second install timeout. A timeout fails the
run. A late installation can leave that APK on the phone; remove only
`org.example.vpnpoc.test` after checking actual package state, and record an
independent cleanup follow-up before another attempt.
The owner must act in Android VPN Settings when the runner opens it. The marker
`.cache/i028-system-revoke-pending.json` signals this step without credentials.
The runner never changes the app-op to manufacture this observation.
After the owner forgets the test profile, the runner confirms the revoked
app-op and brings its own test activity to the foreground so Android does not
park the instrumentation while Settings is open. The client close and each
subsequent refresh have bounded waits. Foregrounding never regrants VPN
permission or counts as the no-restart assertion; native down, server closure,
40 seconds without restart and exact ledger settlement are checked separately.
The previous Settings-timeout receipt remains `passed=false`; the later
`i-028-network-revoke-success.json` is the passing source-bound repeat.

After a failed lifecycle run, preserve its `passed=false` receipt. The shorter
`Test -Suite NetworkRevoke` repeats only the open Wi-Fi/mobile/loss and Settings
revoke criteria, writing `i-028-network-revoke-staging.json`. Build also compiles
the `network_revoke` entrypoint. The radio-loss action checks disabled Wi-Fi and
disconnected mobile data after 15 seconds, plus the native bounded deadline.
Android `settings` reports saved preferences and is not a live radio check. The phone-only
`i-028-radio-reverse-continuity.json` verified that ADB reverse itself survived
this transition. It then requires actual staging egress on cellular and Wi-Fi.
If the phone disconnects from USB anyway, the run fails and a separate phone
restoration receipt is required before another staged attempt.

Lifecycle temporarily changes screen/battery/idle/network state, restoring each
owned setting even if a different restoration fails. For a terminated local
runner, inspect its saved restoration facts and actual phone state. If this test
left a force-idle/battery override, local recovery is `adb shell cmd deviceidle
unforce` and `adb shell dumpsys battery reset`, followed by the original Wi-Fi/data
switch settings and wake. Do not reset an unrelated pre-existing override.

### Remaining physical scenarios: required resources and observations

- Background/screen off: record native window and node deadline, put the app in
  background with screen off for longer than one 90-second window, then wake.
  Accept actual acknowledged renewal or confirmed server cutoff; after wake check
  native state and explicit recovery with independent egress. Do not equate this
  to natural sleep while USB/charging/wake locks prevent suspension.
- Natural sleep: first verify a new, unforced deep-idle history entry in a
  phone-only USB-detached interval. For the separate `NaturalSleep` suite the
  owner temporarily sets a 30-second screen timeout before Test, disconnects
  USB only after `.cache/i028-natural-sleep-ready.json` appears, leaves the
  phone untouched for at least 390 seconds, then reconnects on instruction.
  The driver never forces idle or simulates battery unplug. It requires actual
  USB absence, a new deep-idle entry compared with the pre-sleep history,
  zero live A slots/peers while detached, native down after wake and new real
  staging egress after recovery. The owner restores their former display
  timeout afterwards. If Flutter's device test does not survive unplugging,
  retain the failed receipt and independently restore the phone; do not count
  the phone-only baseline as VPN acceptance.
- Forced Doze: record original battery/idle/display state, force idle only for the
  bounded test, observe actual deep-idle entry and server cutoff/valid renewal,
  then unforce/reset only changes owned by the test and verify wake/recovery.
  Natural sleep must have its own timestamped observation.
- Wi-Fi/mobile: an active SIM and positive cellular data baseline are prerequisites.
  Record original switches; check Wi-Fi to mobile, temporary loss, then mobile to
  Wi-Fi with actual network transport metadata and independent egress. Restore
  switches, inspect native deadlines/client state and settle every connection.
- System revoke: while a real tunnel is active the owner opens Android Settings,
  VPN, DetourVpn and revokes/forgets its permission. Observe native down, server
  closure and settlement; wait beyond a renewal interval to exclude self-restart.
  Regrant requires a later explicit owner connect. `appops` is not this UI action.
- DNS/IPv6: first demonstrate working direct IPv6 for the same probe UID. Use
  fixed synthetic requests and aggregate destination/port/family counts on an
  approved observation network, with no unrelated packets, DNS names or payload
  capture retained. Check included/excluded routing on connect, transitions and
  failures; external IPv4 and a successful DNS answer alone are insufficient.
  Observe before NAT on a controlled test Wi-Fi gateway; the VPN node alone
  cannot see traffic that bypasses it. Keep excluded-UID direct routing as a
  separate positive control, not a leak. Do not retain PCAP, payload, DNS names,
  full addresses or MAC. If pre-NAT observation or positive direct IPv6 is
  unavailable, leave the criterion open. See ADR-064 and
  `docs/i-028-acceptance-matrix.md`. Excluded apps intentionally retain direct
  DNS/IPv6 per ADR-040.

  Для агрегированного наблюдения на подготовленном Linux-шлюзе используйте
  `scripts/i028_packet_audit.py` из корня репозитория:

  ```sh
  sudo python3 scripts/i028_packet_audit.py --interface wlan0 --phase baseline --duration 90 --max-packets 2000
  ```

  Укажите интерфейс, смотрящий в сторону Wi-Fi клиентов, до NAT. Утилита
  интерактивно запрашивает IPv4/IPv6 тестового телефона и IP VPN endpoint без
  отображения ввода; не передавайте эти адреса аргументами командной строки.
  Она запускает `tcpdump` с пределом 256 байт на пакет, без записи PCAP, и
  печатает только счётчики. Признак обрезанного IP-заголовка делает результат
  непригодным для приёмки. Для
  `included` повторите окно с подключённым VPN, для `excluded` — с исключённой
  probe UID. Сначала нужен direct baseline с DNS и global IPv6, а затем отдельный
  положительный `excluded` контроль. При нулевых счётчиках в baseline,
  `packet_limit_reached=true`, ошибке/прерывании захвата или неясном результате
  ничего не принимайте: сохраните только безопасные агрегаты и оставьте ворота
  открытыми. Утилита не выставляет pass/fail и не заменяет физическую проверку.
- Two devices: connect and authorize two actual physical Android devices, keep
  separate keys/accounts and disposable test sessions, compare windows/counters,
  deny cross-owner operations, revoke one and verify the other still transfers.
  The existing one-device runner deliberately refuses two devices; a dedicated
  two-device driver must be implemented and reviewed before that scenario.
  A Wi-Fi-only tablet is suitable for this simultaneous-device scenario; a SIM
  is needed only for cellular handover, which remains a separate test.
- Provider failure: identify an existing separately approved independent provider
  and its fixed test resources first. Current A/B inventory is same-provider.
  Preserve manual node choice and v3 unknown-counter holds (ADR-039); do not add
  automatic migration or copy v2 recovery accounting into v3. No B service changes
  or newly purchased infrastructure are authorized by this runbook.

Each scenario needs its own sanitized receipt, source hashes, actual observations,
settlement and restoration outcome. Missing resources leave that criterion open.

The owner authorized this bounded staging scope on 2026-10-23. No production,
payment, B API/database/Caddy or Telegram configuration changes are included.
Do not start I-026/I-027 from this runbook. ADR-041 defines the privilege boundary.

## Prepare before the 30-minute exposure clock

Use PowerShell 7 in the checkout. Preserve the ordinary debug APK in
`apps/client/build/app/outputs/flutter-apk/app-debug.apk`: physical tests compile
their own entrypoint only in the reviewed ASCII snapshot outside OneDrive.

```powershell
uv run --frozen python -m ruff check infra/staging-v3 scripts/check_i028_bootstrap.py scripts/i028_pipe_relay.py services/api/tests/test_i028_live.py
uv run --frozen python -m mypy scripts/i028_pipe_relay.py scripts/check_i028_bootstrap.py services/api/tests/test_i028_live.py
$env:VPN_I028_LOOPBACK_TEST = '1'
uv run --frozen python -m pytest infra/ansible/tests/test_i028_operator.py infra/ansible/tests/test_i028_transport.py -q
Remove-Item Env:VPN_I028_LOOPBACK_TEST
```

The namespace lab requires WSL root **locally**, pinned native binaries and
Linux iproute2/nftables/iptables/openssl. It has an isolated fake WAN and no real
internet or Android. Systemd supervision/timer scheduling is simulated there.
Use the existing reviewed root Python environment with the pinned node runtime
dependencies; execute `scripts/check_i028_bootstrap.py` inside
`unshare --mount --net`. The receipt must match the current normalized operator
source hash. A stale receipt fails source staging.

```powershell
./scripts/run_i028_staging.ps1 -Action Build -ClientDirectory '<reviewed ASCII snapshot>/apps/client'
./scripts/run_i028_staging.ps1 -Action Stage
```

Build runs locked offline dependency resolution, Dart analysis, Flutter unit
checks, both integration entrypoint configurations and the independent UID probe
APK. It checks the snapshot against current app/native sources. Stage transfers
only an explicit source/public-binary allowlist to the owner staging account and
checks its manifest and Bash syntax. Stage performs no privileged installation.

The WSL NAT environment cannot connect to the Windows API's loopback listener.
`i028_pipe_relay.py` bridges only the three authenticated node paths over private
subprocess stdin/stdout. WSL listens at 127.0.0.1:18767, Windows at
127.0.0.1:18766; neither binds the LAN. SSH reverse binds only
127.0.0.1:18443 on A. The real loopback drill checks byte transfer, a rejected
non-node path, and child/listener cleanup. Its HTTP responder is synthetic and
does not establish node authentication or VPN acceptance.

## Owner/device actions

Connect/unlock exactly one physical Android, authorize USB debugging. Preserve
the existing `org.example.vpnpoc` installation/data: its UID is the independent
probe target. The separate probe APK is `org.example.vpnpoc.test`, built with
native Gradle `-Pi028Probe=true`; the default POC test runner stays unchanged.
The runner refuses an existing client session, an always-on VPN,
or a pre-existing ADB reverse port. It temporarily grants ACTIVATE_VPN to the
test client and restores its prior app-op mode. It does not enable always-on.

Before server preparation, run the phone-only diagnostic:

```powershell
./scripts/run_i028_staging.ps1 -Action Probe
```

It temporarily installs/removes only the independent test APK and requires no
server, API, database or sudo. Inspect `direct_network_ready` separately from
`passed`; this baseline is never VPN dataplane acceptance. The sanitized receipt
is `docs/evidence/i-028-probe-after-startup-fix.json`.

Only when the complete runner is built and the phone is ready:

```powershell
./scripts/run_i028_staging.ps1 -Action Install
```

This opens a visible local SSH terminal. Enter the **staging vpn-deploy sudo
password there only**. Never send a password to chat. The fixed root installer
creates two closed slots, a private ephemeral localhost certificate, a narrow
fixed-verb sudo grant and an independent 30-minute cleanup timer. The timer
starts before network exposure and is never extended by retries/status calls.
If the already active timesyncd service reports an unsynchronized clock and has
no link-specific NTP configuration, the installer selects only the two Cloudflare
IPv4 servers verified reachable from A (162.159.200.1/162.159.200.123) with
`timedatectl ntp-servers ens3`, restarts timesyncd and waits up to 60 seconds.
It preserves main configuration files/timezone and refuses an existing per-link
choice. Any installer failure reverts this override. Successful installation
retains the functional runtime time source (until reboot/revert), separately
from temporary VPN resources. This prerequisites fix does not bypass the clock gate.
Manual rollback, if required: `sudo timedatectl revert ens3` then
`sudo systemctl restart systemd-timesyncd.service`. See
[systemd runtime NTP commands](https://www.freedesktop.org/software/systemd/man/254/timedatectl.html)
and [Cloudflare NTP addresses](https://developers.cloudflare.com/time-services/ntp/usage/).

```powershell
./scripts/run_i028_staging.ps1 -Action Status
./scripts/run_i028_staging.ps1 -Action Test
```

Test requires at least 1,100 seconds remaining before it provisions a fresh node
in disposable local PostgreSQL. Node credentials and header keys exist only in
an owner/SYSTEM ACL private temporary directory, root private runtime files and
process memory. Raw Flutter/instrumentation/SSH manifest output is captured and
never echoed. Test-only OTP/funding/fault routes exist only in the local pytest
application. Test credentials/free quota are not a real Telegram/payment flow.

The seed phase retains encrypted session data; the run phase must use a different
actual Android PID with the same real local account, session family and device.
Subsequent checks use the production window controller/native adapter and actual
staging agent/gate/peer implementation. The independent probe sends only fixed
HTTPS IP checks (ipify and a fixed AWS checkip fallback) and one fixed example.com
DNS question; it records booleans and bounded failure categories,
never the user's direct IP, browsing destinations or payloads. The IPv4/IPv6
services are documented by [ipify](https://www.ipify.org/) and
[AWS checkip examples](https://docs.aws.amazon.com/cli/latest/userguide/bash_cloudformation_code_examples.html).

The current automated slice attempts >3-minute acknowledged renewals, included and
excluded probe UID routing, UDP DNS reachability, IPv6 denial (baseline-dependent
coverage), explicit/remote disconnect, combined API/agent loss, kernel/native
expiry, restart reconciliation, and exact measured ledger settlement.
Natural sleep/forced Doze, Wi-Fi/mobile transitions, system permission revoke,
Disconnect versus an in-flight renewal, exhaustive DNS leak capture, two real
simultaneous devices and provider failure remain separate open cases. A failed
or unsupported case must stay open; never infer it from this slice.
An unavailable direct IPv4 baseline does not block the remaining tunnel checks,
but the included/excluded egress comparison remains unaccepted and the full
receipt cannot pass. A stale VPN capability block in `dumpsys vpn_management`
is not a live VPN check; use the probe's actual active network metadata.

## Cleanup and evidence

The runner requests revoke/receipts while API/transport remain available, stops
the agent, invokes scoped root cleanup, stops SSH/pipe/API, removes its ADB
reverse/test APK, restores the prior VPN app-op and ordinary client APK. It
preserves the POC data. An interrupted process still has independent kernel
deadlines and the independent host cleanup timer.

```powershell
./scripts/run_i028_staging.ps1 -Action Finish
```

Finish uses only the scoped helper while its grant exists. After successful
cleanup the grant is removed: a later sudo denial is expected, not evidence
that cleanup failed. Inspect the recorded cleanup receipt and a fresh read-only
host baseline. Never flush/restore global firewall rules or delete an unverified
directory. Root-owned aggregate journal and a cleanup marker remain private for
audit; runtime credentials, slot/header/certificate keys and grants are removed.
The public root-owned release remains recoverable. A second install deliberately
refuses old paths by default. For an explicitly closed prior run, first perform
the phone-only probe and inspect its bounded diagnostics. Then:

```powershell
./scripts/run_i028_staging.ps1 -Action Build -ClientDirectory '<reviewed ASCII snapshot>/apps/client'
./scripts/run_i028_staging.ps1 -Action Stage -RestartClosed
./scripts/run_i028_staging.ps1 -Action Install -RestartClosed
```

Stage only updates the owner source allowlist after local prior cleanup/post-check
receipts. It does not archive or start anything privileged. Stage also permits
the specifically diagnosed legacy timer-reporting failure when all
other component cleanup facts passed and a fresh read-only timer property query
returns `inactive`; other incomplete cleanup still refuses source restaging.
The owner installer
independently validates actual root state with `archive_closed.py`, disarms the
old timer, and moves only fixed closed-release/node/state/run paths into a private
recoverable archive. Existing services/rules/keys, linked receipts, or ambiguous
state refuse restart. The private journal and wrapper are retained; partial move
failures preserve data for targeted audit. `/run` can require a cross-filesystem
copy. The fresh installer then performs its ordinary preflight and creates a new
30-minute deadline. Default install never silently overwrites the previous run.

The revised probe waits for application initialization. Its network-error fields
are fixed categories, not raw exception text. The runner records node aggregate
transfer counters, accounting and settlement even when egress fails early, before
stopping the disposable database. These fields diagnose a failure; handshake,
Android VPN state or nonzero counters alone never certify internet egress.

Sanitized receipts live under `docs/evidence/i-028-*.json`. `passed` applies only
to the explicit receipt scope. A successful local bootstrap/pipe/build does not
mean a remote VPN or phone check has passed. Review open cases before marking
I-028 complete. No public release/supply-chain/SBOM acceptance is implied.

No new app/runtime library was added. Local test tooling uses distro iptables
(GPL-2.0); uv is a pinned upstream bootstrap tool with its upstream MIT/Apache
licensing, and existing pinned Python/native dependencies retain their prior
license/security review boundaries.

## Two-device preflight (read-only)

Run `scripts/run_i028_staging.ps1 -Action Build -Suite Main`, then
`scripts/run_i028_staging.ps1 -Action TwoDevicePreflight` while both intended
physical Android devices are ADB-authorized. This validates exactly two physical
devices, source-bound probe APK hashes, absence of isolated test packages, and
absence of an existing Android Always-on VPN policy. It makes no device or
staging changes and does not run a tunnel. The current receipt records the Pixel
8 Pro and Wi-Fi-only tablet as ready; the tablet needs the base probe APK. The
dedicated simultaneous-window, cross-owner denial and isolated-revoke driver is
still required before starting an I-028 staging window.

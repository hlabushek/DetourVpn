# I-032 — Windows beta client

**Status:** plan approved by the owner on 2026-11-10. Nothing implemented yet.
ADR-095 (to be written in stage 0) amends ADR-090, which lists Windows clients
as disabled during beta.

## Context

Only Android has a beta today: Flutter client `apps/client` (no `windows/`
folder) on the AmneziaWG GoBackend engine in Java. Owner-chosen scope:

- **Distribution:** unsigned portable zip via the Telegram channel with SHA-256,
  same as the APK.
- **Features:** sign-in, device registration, full-tunnel connect, **app
  exclusion**, and **domain-based routing** (mandatory, owner requirement).

Domain routing on Windows follows ADR-079 semantics unchanged: one split toggle
and one direction (`exclude`/`include`) shared by the Domains and Apps tabs;
apps and domains combine with OR; `*.example.ru` matches the apex and
subdomains; no guessing from shared IPs; rules never go to the API; engine
failure stops the VPN instead of falling back to direct. The owner approved
GPL/source compliance for Amnezia Box on 2026-11-03 (ADR-079); the Windows
build uses the same upstream `v1.260910.0`.

Current blockers:

1. ADR-090 lists "Windows/Linux clients" under "Disabled during beta".
2. The backend accepts only `platform="android"`: `Literal["android"]` in
   `services/api/src/vpn_api/devices.py:58` and `CheckConstraint
   ck_devices_platform` in `db/models.py:202`.
3. All native calls go through one MethodChannel `project.vpn/tunnel.v1`, whose
   only handler is Java.

Already available: pinned Flutter 3.47.7 with windows-x64 artifacts, VS 2022
C++ with Windows SDK 26100 (`flutter doctor` green for Windows), go1.25.5 in
`.cache\toolchains`, Amnezia Box sources in `third_party/amnezia-box`. The
client has no third-party plugins, so Windows compatibility depends only on our
channel.

## Key decisions (to record in ADR-095)

- **Engine: Amnezia Box (sing-box fork with AWG) as a separate process
  `detour-engine.exe`.**
  - TUN via wintun.dll; route rules `process_path` / `process_name` and
    `domain` / `domain_suffix` give app exclusion and domain routing in one
    engine, without a custom WFP driver.
  - Domains are identified only by sniffing (TLS SNI, HTTP Host, QUIC). DNS is
    hijacked (`hijack-dns`) and resolved by the same rules. IP→domain reverse
    mapping from the DNS cache and fakeip stay disabled (ADR-079: no guessing
    from shared IPs). Unidentified names follow the base direction.
  - Control-plane bypass uses the same mechanism: an "API IP → direct" rule, the
    analogue of Android's own-package exclusion (ADR-078).
  - We write an adapter, not a tunnel (AGENTS.md).
  - Risk: Amnezia Box failed on Android at `awg_device_up` (ADR-080), so
    **stage 1 is a gated spike**.
  - amneziawg-windows has no domain routing, so there is no full fallback. If
    the spike fails, debug Amnezia Box (build, `awg` endpoint, wintun); the
    replacement candidate is sing-box-lx (ADR-080). The owner picks the engine
    in that case.
- **Elevation:** exe manifest `requireAdministrator` (TUN and routes need
  admin); UAC prompt at launch. No separate service in the beta: simpler,
  smaller attack surface.
- **Keys and storage: Windows CNG and DPAPI only, no new dependencies.**
  - X25519 via `BCRYPT_ECDH_ALGORITHM` + `BCRYPT_ECC_CURVE_25519`;
    HMAC-SHA256 via CNG.
  - Vault: `CryptProtectData` (CurrentUser) in `%LOCALAPPDATA%\DetourVpn\vault`.
  - The private key never reaches Dart or the backend (parity with
    `SecureVault` and `KeyConfirmation.java`).
- **Config delivery to the engine:** preferably stdin; otherwise a temp file
  with an owner-only ACL, deleted right after start. Never log the config.
- **Device platform:** `windows`. Needs a migration and an API deploy to beta;
  the owner deploys.
- **Licences:** Amnezia Box is GPL-3.0 — ship sources or a link to the exact
  commit and patches in the zip, update `THIRD_PARTY_NOTICES.md`. The prebuilt
  wintun.dll signed by WireGuard LLC is redistributable; install with hash
  verification.
- **Issue number:** I-032. The ROADMAP row I-022 "Windows/Linux" clashes with
  the I-022 file (manual subscriptions); note it in ROADMAP.

## Stages

### Stage 0. Decisions and scope (docs only)
- This file — acceptance criteria (see "Verification").
- `docs/adr/095-windows-beta-client.md` — engine, elevation, storage, unsigned
  distribution, what is disabled (Always-on, battery), and that domains are
  enabled on Windows per ADR-079. Amends ADR-090.
- `ROADMAP.md` — link to I-032.

### Stage 1. Engine spike (gate, no UI)
- `scripts/build_engine_windows.ps1`, modelled on
  `scripts/build_amnezia_box_android.ps1`:
  - check go1.25.5 and the source zip SHA from `source-lock.json`;
  - apply `peer-stats.patch`;
  - `go build` for `windows/amd64` with tags `with_awg,with_wintun` (confirm
    tags against source-lock);
  - build under `%LOCALAPPDATA%\ProjectVpn\`, outside OneDrive;
  - separate step: download the pinned wintun.dll and verify its SHA-256.
- `windows/runner/config_factory.cpp` — JSON config builder, a port of
  `LibboxConfigFactory.java` and `DomainRule.java`
  (`apps/client/android/app/src/main/java/org/example/vpnpoc/`):
  - TUN inbound and `awg` endpoint;
  - `route.rules` in order:
    1. `action: sniff`;
    2. `hijack-dns`;
    3. API IP → direct (always);
    4. `domain` / `domain_suffix` + `process_path` → direct (exclude) or → awg
       (include);
    5. `final` = awg (exclude/all) or direct (include);
  - IPv6 blocking and fixed DNS;
  - DNS rules mirroring routing.

  Port domain normalization one-to-one (case, IDNA, one trailing dot, reject IP
  literals and malformed labels), using the Java test vectors so both
  implementations agree. Port the `Profile.parse` / `ClientProfile.parse`
  checks (reject remote app directives, custom routes, IPv6, DNS hostnames) to
  C++ next to the adapter, with tests.
- **Gate:** the owner runs the built engine manually with their device config
  and confirms handshake and server egress via the read-only peer status
  (ADR-081), plus one test domain in exclude mode going direct while the rest
  goes through the VPN. Do not proceed without these confirmations.

### Stage 2. Flutter Windows platform
- `flutter create --platforms=windows .` in `apps/client`, then clean up the
  template: name `DetourVpn`, icon, `requireAdministrator` manifest.
- The exe version comes from `pubspec.yaml` automatically (`FLUTTER_VERSION` in
  CMake), so there is no second place to bump.

### Stage 3. Native adapter `windows/runner`
- `tunnel_channel.cpp/.h` — handler for `project.vpn/tunnel.v1` with the same
  method allowlist as `ClientActivity.java`:
  - `permission` — admin rights check;
  - `publicKey`, `prove`;
  - `connect` / `connectWindow` / `advanceWindow` / `disconnect`;
  - `snapshot` (up, handshake, bytes from the engine);
  - `sessionRead/Write`, `pendingLoginRead/Write`;
  - `openLogin` — only `https://t.me` via `ShellExecuteW`;
  - `apps` — app list (exes from Start-menu shortcuts and running processes,
    plus a file picker);
  - `splitWrite`.

  Unknown methods return `notImplemented`.
- `secure_vault.cpp` (DPAPI), `key_confirmation.cpp` (CNG X25519 + HMAC, same
  transcript `project-vpn:key-confirmation:v1:...`).
- `engine_process.cpp`:
  - start/stop `detour-engine.exe` in a Job Object so it dies with the app;
  - stats via the engine's local control interface, loopback only;
  - window deadline timer modelled on `WindowDeadline.java` (90 s).
- `capabilities`: `awg3`, `userspace`, `ipv4-full-route`,
  `ephemeral-device-key`, `app-split.v1`, `domain-routing.v1`. `splitWrite`
  with domains is accepted only with `domain-routing.v1`, as in
  `ClientActivity`.

### Stage 4. Dart client
- `connection.dart:340` and `window_connection.dart:157` — send `platform` by
  OS (`Platform.isWindows ? 'windows' : 'android'`).
- `settings_screen.dart` — hide "Работа в фоне" and Always-on on Windows; do not
  call `always_on_recovery.dart` / `alwaysOnRecoveryMain`.
- `diagnostic_report.dart` — on Windows save the report via a file dialog
  instead of the share sheet.
- `split_apps.dart`:
  - modes `all` / `exclude` / `include`;
  - show the "Домены" tab by capability `domain-routing.v1`, not by OS
    (hidden on Android/GoBackend, visible on Windows); tie the ADR-084 forced
    domain clearing to the capability too;
  - "Приложения" tab by exe path, icons optional;
  - hint on ADR-079 limits: DoH/ECH and caches can hide a name.
- `protected_api_transport.dart` — no relay needed (the engine rule bypasses
  the API); branch by platform.
- Tests: extend fakes in `apps/client/test/*` for Windows (platform=windows,
  hidden items, refusal without capability).

### Stage 5. Backend
- `services/api/migrations/versions/0022_device_platform_windows.py`:
  `ck_devices_platform` → `platform IN ('android','windows')`. Downgrade only
  when no windows rows exist; otherwise fail closed.
- `devices.py:58` → `Literal["android","windows"]`, `db/models.py:202`;
  `test_devices.py` checks windows is accepted and ios still rejected.
- Update the OpenAPI enum. Admin already shows `d.platform`.
- The owner deploys to beta via the existing `release_beta.ps1` (API step).

### Stage 6. Build and release
- `scripts/build_windows_client.ps1`, modelled on
  `scripts/build_flutter_client.ps1`:
  - check the Flutter revision against `toolchain.json`;
  - copy sources to `%LOCALAPPDATA%\ProjectVpn\builds\<guid>`;
  - `pub get --enforce-lockfile`, format, analyze, test;
  - `flutter build windows --release --dart-define=VPN_WINDOW_TUNNELS=true
    --dart-define=VPN_WINDOW_BUDGET_BYTES=16777216 --dart-define=API_ORIGIN=...`;
  - copy `detour-engine.exe` and `wintun.dll` with hash checks;
  - build `DetourVpn-windows-x64-<version>.zip` and compute SHA-256.
- `scripts/verify_windows_zip.py`, modelled on `verify_beta_apk.py`: zip
  contents, exe resource version, component hashes. Receipt
  `docs/evidence/i-032-beta-windows-<ts>.json` in the same format
  (`artifact_sha256`, `version_name`, `distribution: not_accepted`,
  `signature: unsigned`).
- `scripts/release_beta.ps1` — add a `-Windows` switch without changing the APK
  path.
- `docs/runbooks/beta-channel-kit.md` — channel post (in Russian, tester-facing):
  how to unpack, SmartScreen warning ("Подробнее → Выполнить в любом случае"),
  UAC, SHA-256 check, how to remove.

## Verification (I-032 acceptance criteria)

1. Automated:
   - `ruff`, mypy, `pytest services/api -k devices`, migration up/down test;
   - `dart analyze`, `flutter test`;
   - C++ unit tests for config_factory and profile checks (gtest via a CMake
     target);
   - `build_windows_client.ps1` completes; `verify_windows_zip.py` passes.
2. Manual on a real Windows PC; the owner initiates Connect:
   - launch from zip, SmartScreen and UAC;
   - Telegram sign-in;
   - device registered with `platform=windows` (visible in admin);
   - **handshake and egress confirmed by the server's read-only peer status,
     not the UI**;
   - excluded app goes direct, others through the VPN (compare external IP);
   - domain routing:
     - exclude mode: `*.example.tld` goes direct, a neighbouring domain through
       the VPN;
     - include mode: the reverse;
     - `notexample.tld` is not matched;
     - with the engine killed, traffic does not go direct (fail closed);
   - API reachable while the tunnel is up;
   - Disconnect and closing the window kill the engine; routes and DNS are
     restored;
   - window renewal every 90 s works.
3. Record everything in a sanitized receipt: no IPs, keys, or traffic logs.

## Known beta limitations

- Unsigned exe, no auto-update.
- No kill switch, no Always-on.
- x64 only.
- Domain rules are not guaranteed with in-app DoH/ECH or cached answers
  (ADR-079).
- UAC prompt at every launch.

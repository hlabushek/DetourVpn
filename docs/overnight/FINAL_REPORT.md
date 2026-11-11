# Final report — overnight run 4 (2026-11-11)

Plain English. **Nothing was pushed. Nothing was deployed. No server, VPS or panel was touched.
Nothing was verified on a phone or on a Windows PC.** All results below are from this computer:
unit tests, builds and offline engine checks.

Branch: `claude/overnight-integration` (new, from `claude/redesign-devices-servers` at `ddf1320`).
Plan: `docs/overnight/FINAL_PLAN.md`.

## Task status
| # | Task | Status | Short result |
|---|---|---|---|
| T1 | Commit hygiene | **Done** | 7 area commits + 1 per later task; nothing left uncommitted for ownership reasons (`UNCOMMITTED_OWNER_REVIEW.md`). |
| T2 | Full API test result | **Done** | 1358 passed / 14 skipped / 0 failed (before run 4 code); 1379 / 14 / 0 at the end. |
| T3 | Windows VLESS `with_utls` engine | **Done (offline)** | Rebuilt from pinned source with no download; self-test passes on the new engine and fails on the old one. ADR-112. |
| T4 | Panel servers delivered to devices | **Done in code, off by default** | Same list with `source`/`protocol`; Windows connects via `ConnectVless`; Android shows the Russian "not supported" text. ADR-115. |
| T5 | Android VLESS engine wiring | **Partial** | `with_utls` AAR built offline; libbox probe behind the gate (still off). libbox **not bundled** in the APK; exact blocker in ADR-114. |
| T6 | Signed update manifest | **Done in code, waiting for your key** | Ed25519 envelope; API and Android verify; keys empty until you run the keygen. Android 13+ only. ADR-107. |
| T7 | Subscription formats | **Done (verified formats only)** | Plain/base64 link lists parsed (3x-ui docs, Remnawave source). JSON/Clash/encrypted refused. Not wired to an admin endpoint. ADR-116. |
| T8 | Handshake diagnostics | **Done (diagnostics only)** | Native "handshake seen" memory on Android and Windows feeds the report line. No new stop code. RCA §10. |
| T9 | Checks | **Done** | All listed below passed. |
| T10 | Builds | **Done** | Android debug + release APK (release unsigned), Windows zip. |
| T11 | Docs | **Done** | This file, ADRs 107/111–116, runbooks. |

## Commits (none pushed)
| Hash | Message |
|---|---|
| `91a0633` | feat: connection robustness, pool nodes, latency and handshake diagnostics (ADR-096..102) |
| `8a10e3d` | feat(auth): store the Telegram username from Mini App login (ADR-110) |
| `dbfce44` | feat: panel server import, admin user list and Windows VLESS config (ADR-105, 111..114) |
| `25ac047` | feat: in-app update for Android (ADR-107) |
| `0d7db5d` | feat: user DNS choice on Android and Windows (ADR-106) |
| `952a090` | feat(client): orbit as second tap target; allow screenshots (ADR-103, ADR-104) |
| `a5a1b93` | docs: ADRs 096-114, overnight reports and runbooks |
| `c7366d2` | build(windows): engine with with_utls for VLESS + Reality; config self-test (ADR-112) |
| `c09ff26` | feat: deliver panel servers to devices; VLESS connect on Windows (ADR-115) |
| `75e4ef9` | feat(android): with_utls libbox AAR build and libbox VLESS probe behind the gate (ADR-114) |
| `8193787` | feat(api): parse verified 3x-ui/Remnawave subscription bodies (ADR-116) |
| `851c56e` | feat: Ed25519-signed in-app update manifest (ADR-107) |
| `55a5e74` | feat: native handshake-seen memory for diagnostics on Android and Windows (task 7) |
| (last) | docs: final run 4 report, runbooks, test filler fix |

Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The task text
asked for a Haiku 5.5 line, but this run was Opus 5.5, so the trailer names the model that did the
work. Mixed files went into the commit of their main area; some intermediate commits may not
build alone. The branch tip builds and passes all checks.

## Files left uncommitted
None for ownership reasons. `.claude/launch.json` is machine-local and now ignored. See
`UNCOMMITTED_OWNER_REVIEW.md`.

## Checks run (real results)
| Check | Result |
|---|---|
| pytest, full API + infra suite, throwaway PostgreSQL | **1379 passed, 14 skipped, 0 failed** (end state) |
| pytest `test_panel_delivery.py` after its last edit | 3 passed |
| Ruff check + format on all 12 Python files changed in run 4 | passed |
| mypy on `app_update`, `panel_sources`, `panel_delivery`, `window_routes`, `admin`, `config`, `main` | no issues |
| OpenAPI export `--check` | current |
| `flutter analyze` (in both build scripts) | no issues |
| `flutter test`, full client suite (Android build script and Windows build script) | **182 passed** each |
| Gradle `:app:testDebugUnitTest` | **81 JUnit tests, 0 failures** (incl. `UpdateSignatureTest` 5, `HandshakeMemoryTest` 3, `LibboxVlessEngineTest` 7, `VlessEngineGateTest` 11) |
| Gradle `:app:lintDebug` | passed |
| Windows C++ tests (MSVC 2022, ctest: `config_factory_tests`, `adapter_tests` incl. new VLESS and handshake checks) | **2/2 passed** (several runs; last inside the Windows build script) |
| Engine self-test: `detour_config_tool --vless-selftest` → `detour-engine check` | new engine exit 0; old engine exit 1 ("uTLS … not included") |
| Standalone javac `-Xlint:all -Werror --release 17` + JUnit for the new Java classes | 18 + 5 + 3 passed |

Environment note: both build scripts must be started from a normal terminal (or `powershell
-File` with OS-level redirection). Under Windows PowerShell 5.1, redirecting all streams with
`*>` turns a harmless Gradle/Go warning on stderr into a stop. That happened twice and was not a
code failure. `build_amnezia_box_android.ps1` now handles it like the Windows engine script.

## Artifacts (git-ignored, on this PC only)
| Artifact | Bytes | SHA-256 | Note |
|---|---|---|---|
| `apps/client/build/app/outputs/flutter-apk/app-release.apk` | 46,779,355 | `0d724a8e22dcdefdd0ad9d84997c9b7acddc9fd7ed588887c52d8d81805cedc6` | **Unsigned** (`apksigner`: does not verify); no API origin. Not a release. |
| `apps/client/build/app/outputs/flutter-apk/app-debug.apk` | 127,133,242 | `a4514d93d4a04066cc12b5ddb10d34b98ea08b8a3d0080b9874ff18389af26bf` | Debug key, no API origin. |
| `apps/client/build/windows-zip/DetourVpn-windows-x64-0.1.0-beta.26.zip` | 23,506,890 | `7d9054d792dcdcdfd75c532ab065f58fa4848eefd02263ba21d9f4d69b0d3c47` | Unsigned portable zip, contains the `with_utls` engine. |
| `%LOCALAPPDATA%\ProjectVpn\engine-windows-utls\out\detour-engine.exe` | 25,773,056 | `56eebc1cd4ff6356eb84349e6359b4093ea8e170519d6eeb1e06018b806f0869` | Tags `with_awg,with_gvisor,with_utls`. |
| `.cache/amnezia-box-build/libbox-1.260910-arm64-x86_64-utls.aar` | 34,185,677 | `9a0bdcd56dec271fd0186b746951ef7bd853afa7c3bf14cd49fdbdb01fc55908` | Not used by the APK yet. |

The version name is still `0.1.0-beta.26`; bump it before any real release.

## Decisions made for you (please review)
1. `with_utls` is in **both** engine tag lists. There is one engine for AWG and VLESS, and the
   tag only adds code a config must ask for.
2. Panel delivery is **off** until `VPN_DELIVER_PANEL_SERVERS=true`. Reason: one shared panel
   client, unmetered by our quota, with no per-user revocation.
3. A panel VLESS session on Windows lasts at most 12 h, then stops like an expired window.
4. Android does **not** bundle libbox yet. That needs a second Go runtime in the VPN process, a
   single-tunnel rule and a device regression test.
5. Update signing uses platform Ed25519 only. Android 7–12 get no in-app update (fail closed).
   No crypto library was added.
6. The subscription parser accepts only the verified link-list formats and does not fetch URLs.
7. Handshake memory is used for diagnostics only. No new stop code was added.

## What you need to do
- Review and, if you agree, push `claude/overnight-integration` (I did not push).
- One time: `scripts/app_update_keygen.py` with a key path **outside** the repo. Paste the public
  key into `app_update.py` and `UpdateSignature.java`, then rebuild (`DEPLOY_RUNBOOK.md` §4).
- Build real releases yourself with your signing variables and `-ApiOrigin`.
- To use panel servers on Windows, follow `docs/runbooks/panel-servers-windows.md`.

## Device test needed (not done by me)
**Phone (Android):**
- In-app update on Android 13+: signed manifest accepted, tampered/unsigned refused, install over
  an older build. On Android 12 or lower: no dialog.
- DNS choice really used through the tunnel.
- Orbit and bottom button: one tap = one action.
- Fault tolerance (ADR-096..099): screen off 60 min, swipe away, `am kill`, reboot, Wi-Fi/mobile
  switches, airplane mode.
- Diagnostic report: `Handshake:` line after a `lease_expired`, with the screen on and off.
- Panel server row shows «Серверы из панели (VLESS) пока не поддерживаются на Android…» and
  cannot be selected.

**Windows PC:**
- AWG connection still works with the new `with_utls` engine (regression).
- Panel server: listed with «Сервер из панели (VLESS)», connects, exit address is the panel's,
  disconnect and reconnect after a network change.
- DNS row and the diagnostic `Handshake:` line.

## Deploy
No deploy was done. Order and commands: `docs/overnight/DEPLOY_RUNBOOK.md` and
`docs/runbooks/beta-fault-tolerance-rollout.md`. Panel delivery: `docs/runbooks/panel-servers-windows.md`.

## Recommended next issue
Android VLESS: an ADR for the single-tunnel rule, then bundle the `with_utls` libbox AAR behind
the gate and run a device AWG regression and VLESS test.

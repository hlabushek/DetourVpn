# Overnight report — 2026-11-11

Plain-English summary. Nothing was deployed, no server was touched, and **nothing was committed**.
Your uncommitted work was kept as-is. Files that already had your changes were edited in place,
so committing them now would mix our changes with yours. Review with `git diff` first.

## Read this first
- **No APK or Windows build was produced.** Flutter SDK is not installed here, and Java is 1.8
  (the Android build needs a newer JDK). So: no Dart analysis, no `flutter test`, no Gradle run,
  no artifacts with SHA-256.
- **Nothing was verified on a phone or on Windows.** Any tunnel, DNS or handshake claim needs a
  physical device test.
- Python checks did run (details below).

## Task status

| # | Task | Status | Short result |
|---|---|---|---|
| 1 | Remove screen blocking (FLAG_SECURE) | **Done (code)** | Removed from client app and old PoC app. ADR-103. Privacy protection removed on purpose. |
| 2 | In-app update from our server | **Partial** | Backend manifest endpoint, release script, decision and hash logic done. Download, installer, dialog, FileProvider **not built**. Manifest not signed. ADR-107. |
| 3 | DNS choice | **Partial (Android only)** | Setting, persistence, config rewrite at connect time done for Android. **Windows not wired.** Not tested on device. ADR-106. |
| 4 | 3x-ui / Remnawave (VLESS + Reality) | **Feasibility only** | No engine exists for VLESS/Reality in this app. Recommendation: read-only subscription import, new engine later. ADR-108. |
| 5 | Admin user list: Telegram ID, search, sort | **Done (code), DB test not run** | Telegram ID shown and sortable. **Telegram username is not stored anywhere**, so it cannot be shown or searched. ADR-105. |
| 6 | Orbit as second tap target | **Done (code), Flutter not run** | Orbit and bottom button use one decision and one gate; quick double-tap fires once. ADR-104. |
| 7 | Tester report "Awaiting handshake" | **Root cause analysed; not fixed** | Found the likely misattribution in code. "Handshake corrupted" does not exist in the code. Needs the diagnostic report. `docs/overnight/TASK7_handshake_rca.md`. |

## Task details

### Task 1 — screen blocking
- Only blocking flag in the repo: `FLAG_SECURE` in `ClientActivity.java` (client app) and `MainActivity.java` (old PoC app). Both removed.
- Effect: screenshots and recents previews are allowed again. Owner must accept that this removes a privacy protection.
- Files: `apps/client/android/app/src/main/java/org/example/vpnpoc/ClientActivity.java`, `apps/android/app/src/main/java/org/example/vpnpoc/MainActivity.java`.
- Not verified: build. The unused `WindowManager` imports were left in place.

### Task 2 — in-app update (partial)
- Done:
  - `GET /api/v1/app/update-manifest` reads one JSON file (setting `VPN_APP_UPDATE_MANIFEST_FILE`). Missing or invalid → 404 `update_unavailable`. HTTPS-only URL, 64-hex SHA-256, no downgrade.
  - `scripts/prepare_app_update.py` builds the manifest from an APK (local only).
  - `UpdatePolicy.java`: blocking if below minimum, optional if newer, none otherwise; HTTPS check; SHA-256 check.
- Not done: download, install hand-off, `REQUEST_INSTALL_PACKAGES`, FileProvider, dialog, signing.
- Checks run: 24 pytest tests (`test_app_update.py`, `test_health.py`) passed; the Java logic passed a javac harness; the script ran on a dummy file.
- Files: `services/api/src/vpn_api/app_update.py`, `main.py`, `config.py`, `services/api/tests/test_app_update.py`, `scripts/prepare_app_update.py`, `apps/client/android/app/src/main/java/org/example/vpnpoc/UpdatePolicy.java`, `UpdatePolicyTest.java`.

### Task 3 — DNS (Android only)
- Choices: 1.1.1.1, 8.8.8.8, 94.140.14.14, 9.9.9.9, or default (profile DNS, current behaviour).
- Stored in the `routing` preferences under `dns`. Read/written through the existing channel (`dnsRead`/`dnsWrite`). Changes refused while VPN is on.
- Applied when connecting: the `DNS =` line in `[Interface]` is rewritten before the profile is parsed (`ClientApplication.routedConfig`).
- Not done: Windows (`config_factory.cpp`). The settings row is hidden on Windows.
- Checks: the Java DNS logic passed a javac harness (all checks). JUnit `DnsChoiceTest.java` and Dart `dns_choice_test.dart` were written but **not run**.
- Files: `DnsChoice.java`, `ClientActivity.java`, `ClientApplication.java`, `apps/client/lib/dns_choice.dart`, `apps/client/lib/settings_screen.dart`, tests.
- Note: the setting takes effect at the next connect; an active tunnel keeps its DNS.

### Task 4 — 3x-ui / Remnawave (feasibility)
- The app has no VLESS, Reality, Xray or sing-box tunnel path. Its engines are AmneziaWG/WireGuard only.
- Web search: 3x-ui has a login endpoint and inbound routes whose layout changed between v2 and v3. Remnawave has a public subscription path `/api/sub/{shortUuid}`. Neither was verified against a live panel. The VLESS Reality output format was not confirmed.
- Recommendation: import a read-only subscription URL or `vless://` link. Do not ask for panel admin access. Build a new engine adapter as a separate issue.
- No code written beyond the ADR. ADR-108.

### Task 5 — admin user list
- API: `GET /api/admin/v1/users` now returns `telegram_id` (decimal string or null) for each row, read with one extra query.
- Admin page: "Telegram ID" column appears only when some listed user has one. Sort dropdown: newest, oldest, Telegram ID, status (on the loaded page only).
- Username: not stored by the API, so not shown and not searchable. Your decision needed if you want it stored.
- Checks: 9 static UI contract tests passed (including the new one). The API test `test_user_list_exposes_linked_telegram_id_as_decimal_string` needs PostgreSQL (`VPN_TEST_DATABASE_URL`) and was **skipped**, not run. Ruff and mypy passed on the changed Python files.
- Files: `services/api/src/vpn_api/admin.py`, `apps/admin/static/admin.js`, `index.html`, tests.

### Task 6 — orbit tap target
- The bottom button and the orbit now use the same decision (`resolvePrimaryAction`) and the same gate (`PrimaryActionGate`, 600 ms).
- The orbit is disabled exactly when the button is disabled. No visual change, no label.
- Checks: `primary_action_test.dart` written, **not run** (no Flutter).
- Files: `apps/client/lib/primary_action.dart`, `home_screen.dart`, `apps/client/test/primary_action_test.dart`.

### Task 7 — handshake report
- Strings: "Awaiting handshake" is close to the app's `Ожидается handshake`. "Handshake corrupted" does not appear anywhere in the repo. "Allow the app in the background" is the text for error code `lease_expired`.
- Verified in code: `lease_expired` is set when the granted time window ends, whether or not a handshake ever happened. The app then shows the battery-optimisation hint. That is a likely misattribution, not a proof of Doze.
- Not fixed: a correct fix changes auto-reconnect behaviour and needs a device test. Recommended fix and data to request are in the RCA file.
- The handshake itself is **not** verified.

## Checks that ran (real results)
- `pytest` on `test_app_update.py`, `test_admin_ui_contract.py`, `test_health.py`: **33 passed**.
- `ruff check` and `ruff format --check` on changed Python files: passed.
- `mypy` on `app_update.py` and `admin.py`: no issues.
- OpenAPI `--check`: current after regeneration. The regenerated `packages/contracts/openapi.json` also includes the owner's uncommitted API schema changes (the file was already stale). Review that diff.
- Java logic (`DnsChoice`, `UpdatePolicy`): all checks passed in a javac harness outside the repo.
- Skipped / not run: all PostgreSQL tests (no `VPN_TEST_DATABASE_URL`), Flutter analyze and tests, Gradle unit tests (JUnit files written), Android and Windows builds, device tests.

## Owner decisions I made (please review)
1. Removed FLAG_SECURE from both Android activities, including the old PoC app.
2. Telegram username: not added to the database; the admin page shows only the Telegram ID.
3. Update rule: below minimum supported → blocking, newer → optional, no downgrades. Manifest signing deferred.
4. DNS: four fixed resolvers; default = profile DNS; change applies at next connect; Windows deferred.
5. Orbit debounce 600 ms shared by both controls.
6. 3x-ui/Remnawave: no implementation; subscription-link-first recommendation.
7. Nothing committed; touched owner files were edited in place.

## What you must do manually
- Review `git diff` (many files, including your own uncommitted work).
- Decide whether to commit (suggested: separate commits per ADR, staged file-by-file).
- Install Flutter and a JDK 17 to run `dart analyze`, `flutter test`, and the Gradle tests (listed above).
- Build the APK and the Windows client on your machine (scripts: `scripts/build_flutter_client.ps1` and the project's Android build).
- Test DNS and orbit on a physical phone.
- Ask the tester for the diagnostic report (see TASK7 RCA, section 7). Check it for IPs and keys before sharing.
- Decide on signing for the update manifest before any public release.
- Upload the APK and `app-update.json` yourself when you want the update flow (the runbook has steps).

## Artifacts
- No APK or Windows build was produced. No checksums to report.
- Manifest prep output from a dummy file exists only in scratch space; it is not a release.

See also: `docs/overnight/PLAN.md`, `docs/overnight/TASK7_handshake_rca.md`, `docs/overnight/DEPLOY_RUNBOOK.md`, ADRs 103–108.

---

# Run 2 — 2026-11-11 (overnight, unattended)

Nothing was committed, nothing was pushed, nothing was deployed, and no server was touched.
**Nothing was verified on a phone or on a Windows PC.** Owner files that already had changes
were edited in place; review `git diff` before committing.

## Correction to run 1
Run 1 said Flutter and JDK 17 were missing. They are installed; they are just not on `PATH`:
Flutter 3.47.7 at the pinned toolchain path used by `scripts/build_flutter_client.ps1`, and
JDK 21 inside Android Studio. `where flutter` still finds nothing and `java -version` still
says 1.8. This run used the pinned tools directly, so the Dart, Java and C++ checks below are
real runs. (`flutter analyze` crashes on the OneDrive path with Cyrillic letters; it was run on
an ASCII copy of `lib/` and `test/`, as the build script itself does.)

## Task status after run 2

| # | Task | Status | Short result |
|---|---|---|---|
| 2 | In-app update | **Done in code, not on device** | Start-up check, Russian dialog (blocking/optional), HTTPS download, size + SHA-256 + package/version checks, three downgrade guards, system installer via own read-only provider, `REQUEST_INSTALL_PACKAGES`. Manifest signing still open. |
| 3 | DNS choice | **Android + Windows in code, not on device** | Windows uses a different config generator (sing-box JSON), so it was wired separately. Settings row now shown on Windows. |
| 7 | Handshake report | **Partial (safe part done)** | Message no longer claims Android slept the app. Diagnostic export now says whether the app saw a handshake. New stop code deferred (reason below). "Handshake corrupted" is not in the code. |
| 5 | Telegram username | **Documented** | ADR-109. Not stored by design; the admin page says so. No new personal data. |

## Task 2 — in-app update (Android)
What changed:
- `apps/client/lib/app_update.dart` (new): strict manifest parse, `decideUpdate` (same rule as
  Java), `checkForUpdate` (once per launch, silent on any failure), Russian dialog.
  Blocking: «Нужно обновить приложение», only «Обновить», back button and tap-outside ignored.
  Optional: «Доступно обновление» with «Позже».
- `apps/client/lib/main.dart`: runs the check after start on Android only (Windows builds pass
  no updater; tests pass none, so existing tests are unaffected). Uses a navigator key.
- `AppUpdateInstaller.java` (new): own download thread (not the VPN worker), one download at a
  time, HTTPS only, redirects off, timeouts, file in app-private cache. Checks in order: exact
  size, SHA-256, APK package name = ours and APK version = manifest version > installed.
  Any failure deletes the file. Old files are deleted on the next cold start.
- `UpdateApkProvider.java` (new): the app has no androidx, so instead of androidx FileProvider a
  minimal provider serves exactly one read-only file to the installer. Not exported.
- `ClientActivity.java`: channel methods `appVersionCode`, `appUpdateInstall`; opens Android's
  "install unknown apps" screen when that permission is missing.
- `AndroidManifest.xml`: `REQUEST_INSTALL_PACKAGES` and the provider.
- `apps/client/test/app_update_test.dart` (new). ADR-107 updated (status, Android side,
  permission explanation, verification).
- Not done / open: manifest signing (Ed25519, key offline) before any public release; device test.

## Task 3 — DNS on Windows
- Finding: Windows does **not** share Android's config generation. Android rewrites the
  AmneziaWG text; Windows builds a sing-box JSON in `config_factory.cpp`. So the same setting
  was wired into the Windows path separately.
- `config_factory.h/.cpp`: `RoutingPolicy::dns_server` (empty = profile DNS) and `IsDnsChoice`.
  Only the tunnel resolver address changes; an unlisted value is refused.
- `tunnel_controller.h/.cpp`: stored in the existing prefs file (`dns` line); `Dns`/`WriteDns`;
  refused while connected; applied at connect.
- `tunnel_channel.cpp`: `dnsRead` / `dnsWrite` (same contract as Android).
- `apps/client/lib/settings_screen.dart`: DNS row no longer hidden on Windows.
- Tests: new `ConfigDnsChoice` in `config_factory_tests.cpp` (config generation) and DNS checks
  in `adapter_tests.cpp` `ControllerTests` (persistence, refusal while connected). ADR-106 updated.
- Not compiled: `tunnel_channel.cpp` (needs Flutter types; not part of the test build).

## Task 7 — handshake report
- Grep: "Handshake corrupted" (case-insensitive, all code and doc file types) appears only in
  `docs/overnight/OVERNIGHT_REPORT.md` and `docs/overnight/TASK7_handshake_rca.md`. **It does
  not exist in any code.**
- Why no new stop code: Dart only knows handshakes it has seen. While the app sleeps, native
  renewal keeps the tunnel and Dart may never see the handshake. A "no handshake" code from
  Dart alone could blame the server when the real cause was the phone sleeping (the same
  mistake in reverse). A correct split needs the native side to remember "handshake seen in
  this window" on Android and Windows, plus a device test.
- Done instead (low risk, behaviour unchanged):
  1. `lease_expired` message (`apps/client/lib/connection.dart`) now says the window ended and
     could not be extended, and lists both possible causes. The battery hint stays as
     conditional advice.
  2. Diagnostic report gets an optional line `Handshake: confirmed | not_seen` (Dart
     `diagnostic_report.dart`, Android `DiagnosticReportText.java` + `ClientActivity.java`,
     Windows clipboard path). Fixed values only. Set in `window_connection.dart` on failure.
- Owner runbook `docs/runbooks/beta-lease-expiry.md` still says Android put the app to sleep;
  not edited (owner file).
- Details: `docs/overnight/TASK7_handshake_rca.md` section 9.

## Task 5 — Telegram username
- Verified: `services/api/src/vpn_api/auth/mini_app.py` says Telegram profile fields are never
  persisted. Only the numeric Telegram ID is stored. `AdminIdentity.username` is the admin
  login, not Telegram.
- ADR-109 records the gap and three options (keep as is, recommended; store at login with a
  privacy note, migration and deletion rule; admin-entered alias).
- Admin page (`apps/admin/static/index.html`): one line under the user list,
  «Telegram username не хранится…». No empty column. New contract test.

## Checks that ran in run 2 (actual results)
- `flutter analyze` (whole client): **No issues found.**
- `flutter test` on app_update, dns_choice, primary_action, widget, ui_preview: **29 passed.**
  After task 7: diagnostic_report, window_connection, connection, app_update, dns_choice,
  widget, primary_action, split_apps_windows: **98 passed.**
- `dart format` check: 4 of my files needed formatting and were formatted.
- `javac -Xlint:all --release 17` against Android SDK 36 for `AppUpdateInstaller`,
  `UpdateApkProvider`, `UpdatePolicy`, `DnsChoice`, `DiagnosticReportText`: **no errors, no warnings.**
- JUnit 4 (outside Gradle) `UpdatePolicyTest` + `DnsChoiceTest` (written in run 1): **11 passed.**
- Scratch check of the `DiagnosticReportText` handshake line: ok.
- Windows C++ tests, MSVC 2022, `/W4 /WX`, ctest: **2/2 passed**; `config_factory_tests`
  145 checks, 0 failures. Note: `adapter_tests` started directly from Git Bash fails one
  existing check (line 214, Ctrl-C delivery to the fake engine needs a console); under ctest it
  passes. Not related to the DNS change.
- `pytest` `test_admin_ui_contract.py`, `test_app_update.py`, `test_health.py`: **34 passed.**
- `pytest` `test_admin.py` against a temporary PostgreSQL (project runner
  `scripts/test_with_postgres.py`, throwaway cluster, removed afterwards): **23 passed, 1 error.**
  The run-1 test `test_user_list_exposes_linked_telegram_id_as_decimal_string` **passed**.
  The error is `test_admin_encrypted_backup_restores_identity_audit_and_quota`: no `pg_dump`
  tool path configured for the runner (`postgres_tool_missing`). Environment, not these changes.
- `ruff check` + `ruff format --check` on changed Python files: **passed.**
- `scripts/prepare_app_update.py` on a dummy file: manifest written and accepted by the API
  loader; an `http://` URL is rejected (exit 1).
- Android build: **`scripts/build_flutter_client.ps1` ran end to end and exited 0** (default options: no
  API origin, no window-tunnel flags, no signing keys). It ran: locked `pub get`, `dart format`
  check, `flutter analyze`, the **full `flutter test` suite (170 passed)**, debug APK, Gradle
  `:app:testDebugUnitTest` (**55 JUnit tests, 0 failures**, including `UpdatePolicyTest` 4,
  `DnsChoiceTest` 7, `DiagnosticReportTextTest` 3) and `:app:lintDebug` (passed), then the
  release APK. This compiled `ClientActivity.java` and the merged manifest; `aapt2` confirms
  `REQUEST_INSTALL_PACKAGES` and the non-exported `UpdateApkProvider` with
  `grantUriPermissions=true` in the release APK.
  Artifacts (in `apps/client/build/app/outputs/flutter-apk/`, git-ignored):
  - `app-release.apk`, 46,779,355 bytes, SHA-256
    `2eff0241a77ba7ab4252bbecf56e1db189ac8a1c16f9679ac80ec3106f3b2d10`. **Unsigned**
    (`apksigner`: does not verify) because no beta keystore was given. **Not installable and
    not a release.** It also has no API origin, so it would show the setup screen.
  - `app-debug.apk`, 127,125,062 bytes, SHA-256
    `1770ea3e4cb79e871dc3c02a1c00ac6fc295d0193a253d5ee17ac0eb045a43f1` (debug key, no API origin).
  For a real beta build, run the script yourself with `-ApiOrigin`, `-WindowTunnels` (as your
  beta uses) and the beta signing variables in your own secure shell.
- Windows Flutter client build: **not run** (only the C++ unit tests above).

## What remains
- Manifest signing for the updater (owner decision; key must stay offline).
- Task 7 proper fix: native "handshake seen in this window" flag on Android and Windows, then a
  distinct stop code. Needs a device test.
- `tunnel_channel.cpp` is compiled only by the full Windows Flutter build (not run here).
- Telegram username: owner decision (ADR-109 options).

## Needs a physical phone
- Update: dialog on start, "install unknown apps" screen, download, the system installer,
  update over an older build signed with the same key, refusal of a tampered or older APK,
  cleanup on next start.
- DNS: that the chosen resolver is really used through the tunnel.
- Task 7: a diagnostic report from the tester with the new handshake line.

## Needs a Windows PC
- DNS row, saving, refusal while connected, and that the engine uses the chosen resolver.
- The diagnostic report copy now includes the handshake line.

## Owner decisions (updated list)
1. FLAG_SECURE removed (run 1).
2. Telegram username not stored; admin page says so (ADR-109). Options listed if you want it.
3. Update rule: below minimum = blocking, newer = optional, never downgrade (run 1).
4. **New:** no androidx dependency added; an own one-file provider replaces FileProvider.
5. **New:** update check once per start, silent on failure, Android only.
6. **New:** `REQUEST_INSTALL_PACKAGES` added. Fine outside Google Play; on Play the updater
   must be removed or replaced.
7. Manifest signing still deferred. **Decide before any public release.**
8. DNS: four fixed resolvers, next connect only; **now also on Windows**.
9. **New:** task 7: message softened and export extended; no new stop code until the native
   side can say whether a handshake happened.
10. Orbit debounce 600 ms (run 1). 3x-ui/Remnawave: no implementation (run 1).
11. Nothing committed.

## What you must do in the morning
- Review `git diff` (it mixes your uncommitted work with ours). Commit per ADR if you agree.
- Optional: put Flutter and JDK 17+ on `PATH`; the build script already finds them without it.
- Install the APK on a phone and test the items above. Build the Windows client and test DNS.
- Decide on manifest signing before publishing an update manifest.
- Upload the APK and `app-update.json` yourself only when ready (DEPLOY_RUNBOOK). Nothing
  was uploaded.

---

# Run 3 — 2026-11-11 (overnight, unattended)

Nothing was committed, nothing was pushed, nothing was deployed. No server, VPS or panel was
contacted. **Nothing was verified on a phone or on a Windows PC.** Owner files that already had
changes were edited in place; review `git diff` before committing. Plan: `RUN3_PLAN.md`. Manual
steps: `RUN3_OWNER_STEPS.md`.

## Read this first
- **The Windows engine as built today cannot run VLESS + Reality.** Verified in the pinned source
  archive: Reality needs the `with_utls` build tag, and the Windows (and Android) engine builds do
  not set it. The new config generator is correct by its tests, but the engine would refuse the
  config at start. Adding the tag is your decision (it changes the engine AWG testers use too).
- Panel servers are stored and shown in the admin panel, but **no device receives them yet**.
  There is no delivery path from the API to the client. So no VLESS connection is possible today
  on any platform, and none is claimed.
- Subscription links are **not** supported (formats not verifiable offline). Single `vless://`
  links only.

## Task status

| # | Task | Status | Short result |
|---|---|---|---|
| A | Store Telegram username | **Done in code**, DB tests pass | Stored on the Telegram identity, overwritten on each Mini App login, cleared when absent or invalid, shown/searchable/sortable in admin, deleted with the account. ADR-110 (supersedes ADR-109). |
| B1 | `vless://` Reality parser | **Done in code**, tests pass | Strict pure function, stable `panel_link_*` error codes, no host hard-coded. |
| B1 | Subscription formats | **Not started (blocked)** | Could not be verified offline; open question. |
| B2 | Store panel servers, show source | **Done in code**, DB tests pass | Encrypted link (existing admin Fernet key), audited idempotent import, admin lists show source VPS / panel. ADR-111. |
| B3 | Windows VLESS outbound | **Partial** | Generator + 41 new C++ checks pass. Not wired to the controller; engine lacks `with_utls`. ADR-112. |
| B4 | Android | **Gap recorded** | No VLESS core. Russian text + helper + test exist; nothing shows it yet (no delivery). ADR-113. |
| C | Checks | **Ran** | See below. |

## A — Telegram username
- Source: only the Mini App login (`initData.user.username`). The bot code login has no username
  and leaves the stored value unchanged.
- Rule: Telegram format `^[A-Za-z0-9_]{4,32}$` or NULL. A bad or missing username never fails the
  login; it is stored as NULL, so a removed username clears the old one.
- Admin: `GET /api/admin/v1/users` returns `telegram_username`; `q` accepts `@name` or `name`
  (exact, case-insensitive). UI: «Telegram username» column (only when someone has one), sort
  «По Telegram username», updated hint text.
- Privacy: never logged, never returned to client apps (a test checks `/api/v1/me`), removed by
  account deletion (the `auth_identities` row is deleted in `account.py`). No index: small beta,
  and a functional index risks `alembic check` drift.
- Migration `0026_telegram_username`: one nullable column + check constraint. Rollback drops the
  column; usernames return on the next login.

## B — panel servers (3x-ui / Remnawave)
- Parser `services/api/src/vpn_api/panel_sources.py`: `vless` scheme, canonical UUID, DNS name or
  IPv4 host (IPv6 refused), port, `security=reality`, `pbk` (32-byte key), `sni`, `sid` (0–16
  hex), `fp` (uTLS names), `flow` (empty or `xtls-rprx-vision`), `type=tcp`, `encryption=none`.
  Unknown keys, duplicates, passwords, paths and control characters are rejected. Harmless panel
  keys `spx` and `headerType=none` are accepted and ignored. The `#name` is dropped.
- Storage: table `panel_server_sources` (migration `0027`), link as Fernet ciphertext under the
  existing `VPN_ADMIN_ENCRYPTION_KEY` (same as admin TOTP secrets). Rollback refuses while rows
  exist. No panel password, token or panel API call anywhere.
- API: `POST /api/admin/v1/panel-servers` (admin role, idempotency key, reason, audited; result
  holds only id and label); `GET /api/admin/v1/panel-servers` (no link, host, UUID, key or short
  id); `GET /api/admin/v1/nodes` now has `source: "vps"`. OpenAPI regenerated; the only additions
  are this path and the `PanelServerImport` schema.
- Admin page («Серверы»): source column on VPS nodes, a «Серверы из панели» list, and an
  admin-only import form with a masked link field that is cleared after saving. Russian error text
  for every parser code.
- Windows: `CreateVlessEngineConfig` in `config_factory.cpp` (vless outbound `proxy`, Reality +
  uTLS, xudp, system resolver for the panel host, same routing/DNS/IPv6 rules as AWG). The shared
  routing code moved into one builder; AWG output unchanged (all existing exact-string checks
  pass).
- Android: `apps/client/lib/panel_servers.dart` holds «Серверы из панели (VLESS) пока не
  поддерживаются на Android. Выберите другой сервер.»

### Bug found and fixed during the run
- The import endpoint first encrypted the link including its `#display name`. A raw Cyrillic name
  would have caused a 500 (ASCII encode). It now stores only the checked part, without the
  fragment. The test link now carries a raw Cyrillic name to cover this.

## Files changed in run 3
- New: `docs/adr/110-telegram-username-stored.md`, `111-panel-server-link-import.md`,
  `112-vless-reality-windows-engine.md`, `113-android-panel-servers-gap.md`,
  `docs/overnight/RUN3_PLAN.md`, `RUN3_OWNER_STEPS.md`,
  `services/api/migrations/versions/0026_telegram_username.py`, `0027_panel_server_sources.py`,
  `services/api/src/vpn_api/panel_sources.py`, `services/api/tests/test_panel_sources.py`,
  `apps/client/lib/panel_servers.dart`, `apps/client/test/panel_servers_test.dart`.
- Edited: `services/api/src/vpn_api/admin.py`, `auth/mini_app.py`, `db/models.py`,
  `services/api/tests/test_admin.py`, `test_admin_ui_contract.py`, `test_migrations.py`,
  `test_mini_app.py`, `test_mini_app_validation.py`, `apps/admin/static/admin.js`, `index.html`,
  `format.js`, `apps/client/windows/runner/config_factory.h`, `config_factory.cpp`,
  `apps/client/windows/tests/config_factory_tests.cpp`, `packages/contracts/openapi.json`,
  `docs/adr/109-telegram-username-not-stored.md` (status line only), `docs/overnight/PLAN.md`
  (Run 3 section), this report (Run 3 section).
- `test_migrations.py`: the four hard-coded head revisions now read `0027_panel_server_sources`.
- Run 2 UI contract tests that asserted "username not stored" were rewritten for ADR-110.

## Checks that ran in run 3 (actual results)
- **Full `services/api` suite** on a throwaway PostgreSQL (`scripts/test_with_postgres.py`,
  cluster removed afterwards): **1069 passed, 10 skipped, 1 failed, 15 errors** (9 min 56 s).
  All 16 non-passes were `postgres_tool_missing`: those backup/restore tests look for `pg_dump`
  under the worktree's own `.cache`, which only the main checkout has. Same environment issue as
  run 2, not a code failure.
- **Rerun of those backup/restore/monitor tests** with `VPN_TEST_POSTGRES_BIN` set to the main
  checkout's PostgreSQL tools: **29 passed, 1 skipped, 0 failed**. So backup and restore work
  with the new schema (0027).
- After the fragment fix, the panel/username/Telegram ID database tests were rerun on the final
  code: **16 passed**. Migration tests include the schema-drift check (`alembic check`), both
  new rollbacks, the guarded panel rollback and the username constraint.
- Static tests (`test_panel_sources.py`, `test_mini_app_validation.py`,
  `test_admin_ui_contract.py`): **90 passed**.
- `ruff check`, `ruff format --check` on all changed Python files: **passed**. `mypy` on
  `admin.py`, `panel_sources.py`, `auth/mini_app.py`, `db/models.py`: **no issues**.
- `scripts/export_openapi.py --check`: **current** after regeneration.
- `node --check` on `admin.js`: no syntax errors.
- Windows C++ tests, MSVC 2022, `/W4 /WX`, ctest: **2/2 passed**; `config_factory_tests`
  **186 checks, 0 failures** (run 2: 145).
- Dart, on an ASCII copy: `flutter analyze` of the new files **no issues**; `flutter test
  test/panel_servers_test.dart` **2 passed**; `dart format` applied.
- Engine capability: read-only inspection of the pinned Amnezia Box source archive (see "Read
  this first"). No engine build was run.
- Not run: Android build, Windows Flutter build, any device test, any live panel.
- Tooling note: during the run a regex in `admin.py` briefly held raw control bytes, because the
  shell collapsed `\\` to `\`. Found when the OpenAPI exporter failed to import the file; fixed and
  re-checked (no control bytes remain in any changed file).

## Owner decisions made on your behalf (please review)
1. Username stored on `auth_identities`, Telegram format only, NULL when absent or invalid (a
   removed username clears the old one). Exact case-insensitive search; no index.
2. Only the Mini App login updates the username; the bot code login does not touch it.
3. Panel import is a single `vless://` link pasted by an admin; an admin label replaces the
   link's name.
4. Accepted transport: `type=tcp` + Reality only; `flow` empty or `xtls-rprx-vision`; IPv6 hosts
   refused (IPv4-only beta).
5. Panel links are encrypted with the existing admin key; rotating that key makes stored links
   unreadable (re-import needed), the same limit as TOTP secrets.
6. Windows VLESS defaults: TUN `172.19.0.1/30`, MTU 1500, DNS 1.1.1.1 unless you chose one.
7. Engine build tags **not** changed; adding `with_utls` is your decision.
8. Android text placed in a new Dart file instead of your modified `connection.dart`.
9. Nothing committed.

## Needs a device or live system
- Admin page in a browser against a test API with migrations 0026 and 0027.
- Mini App login with a real Telegram test account that has a username (and again after removing
  it).
- Windows VLESS connection: needs the `with_utls` engine, a delivery path and a live panel.
- Android: nothing to test beyond the string yet.

## Recommended next issue
- Rebuild the engine with `with_utls` (owner decision), then build panel-server delivery to
  Windows devices (per-device panel client, capability flag, controller call) as one issue.

---

# Run 4 — 2026-11-11 (unattended, final)

Full report: `docs/overnight/FINAL_REPORT.md`. Nothing pushed, nothing deployed, no server touched,
nothing verified on a phone or a Windows PC.

## Run 3 API test result (T2), finished
- Full `services/api` suite (plus `infra/ansible/tests`) on a throwaway local PostgreSQL
  (`scripts/test_with_postgres.py`, `VPN_TEST_POSTGRES_BIN` set so the backup tests find
  `pg_dump`), on the committed run 1–3 tree: **1358 passed, 14 skipped, 0 failed** (10 min 32 s).
  The skips are opt-in live/staging/device/browser/POSIX tests.
- After all run 4 changes: **1379 passed, 14 skipped, 0 failed** (11 min 43 s). The run-3 error
  `postgres_tool_missing` is gone because the tool path is now set.

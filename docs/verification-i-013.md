# I-013 verification

Status: technically accepted in bounded staging on 2026-10-21. The actual
Flutter/Android acceptance receipt is `docs/evidence/i-013-staging.json`
(`passed=true`), separate from earlier I-011/I-012 evidence. This is not a
production/public-release acceptance.
Issue: `docs/issues/I-013.md`; decisions: ADR-025; reproduction:
`docs/runbooks/flutter-client.md`.

## Changes

- Flutter login/main UI, system/light/dark theme, scroll/font scaling/semantics.
- API authentication/device/key proof/create/ACK/config/revoke lifecycle.
- One cancellable/idempotent create intent; native stop precedes remote cleanup;
  pending remote removal stays visible and blocks a second lease.
- Native application owner, serialized SDK calls, monotonic bounded lease timer;
  fresh handshake + underlying network + API active required for connected.
- No private-key export, credential/profile persistence or raw error logging.
- Source reuse of reviewed I-011 adapter/parser/crypto (SDK path now module-relative).
- Separate local physical and bounded staging integration entrypoints/controllers.

## Actual results so far

- Flutter 3.47.7 archive SHA256 verified against official release manifest.
- `flutter analyze --no-pub`: no issues (ASCII workspace alias).
- `flutter test --no-pub --timeout 60s`: 22 passed (explicit TEST-ONLY fakes for
  lifecycle tests; real loopback HTTP for transport/session tests).
- Narrow 320x640 view at text scale 2 and both brightness modes: widget tests pass.
- Dart LSP on Unicode path crashed. An ASCII junction initially worked; OneDrive
  later marked files as cloud reparse points, which Dart could read but stat()
  reported missing. The build script now copies only client/native source inputs
  and prepared SDK into a fresh local snapshot outside OneDrive, runs checks
  there, and exports ordinary APKs/reports back to the original project. Private
  API/staging configuration and generated build trees are not copied.
- Android build initially rejected Gradle 8.13/Kotlin defaults; pinned Gradle
  8.14.3/Kotlin 2.2.20, disabled unused resource shrinking without code shrinking,
  isolated default Kotlin stdlib injection from existing tunnel lockfile.
- `flutter build apk --debug --no-pub --target-platform android-arm64,android-x64`: passed.
- `:app:testDebugUnitTest`: 10 tests, 0 failures/errors (JUnit XML).
- Physical Pixel 8 Pro / Android 17: `native_bridge_test.dart` passed with real
  upstream core, local public key, native DOWN, invalid proof/profile/TTL rejection
  and cancellation of pending Android VPN permission. No external tunnel started.
  Receipt: `docs/evidence/i-013-native-android.json`.
- `.\scripts\run_i013_android.ps1`: 1 passed / 391 deselected against disposable
  real PostgreSQL + loopback FastAPI + physical Flutter app. Login UI, public key
  registration/proof, admission closed without false connected, revoked intent,
  phone/reverse/API cleanup passed. Telegram delivery is TEST FAKE ONLY.
  Receipt: `docs/evidence/i-013-local-android.json` (`passed=true`). Temporary
  PostgreSQL stopped and generated cluster removed by runner; port 18766 closed.
- Ruff check/format and mypy for `services/api/tests/test_i013_android.py`: passed.
  Existing I-012 evidence regressions: 4 passed; opt-in physical test initially
  skipped in that command and subsequently actually run as described above.
- PowerShell AST parse: all three new scripts passed.
- `:app:lintDebug`: passed, 0 errors / 1 warning about a newer Gradle patch.
- `:app:assembleRelease` and `:app:assembleDebug` with `-Ptarget=lib/main.dart`:
  passed. Release APK is unsigned; no publishing/signing was performed.
- App and Flutter-hosted tunnel dependency graphs are pinned in separate Gradle
  lockfiles; standalone I-011 tunnel lock preserved byte-for-byte.
- Final clean-source rebuild also exposed missing debug-runtime locks for the
  SDK's `integration_test` native plugin. These were generated with Gradle
  locking retained; the plugin is a dev dependency and must be absent from the
  release APK. The build script's direct Gradle checks now use the client's
  pinned 8.14.3 wrapper, rather than the standalone I-011 wrapper (8.13).
- APK audit: arm64-v8a/x86_64 only, `libwg-go.so` present for each ABI; no
  TEST-ONLY or test-route markers in inspected code assets; backup disabled;
  release cleartext disabled and debug network policy absent. Hashes/facts:
  `docs/evidence/i-013-apk-audit.json`. Marker scanning is a bounded check, not
  a complete secret/supply-chain audit or proof of live behaviour.
- Approved `run_i013_android.ps1 -Staging`, 2026-10-21: **1 passed, 391
  deselected in 135.33s**. Physical Pixel 8 Pro / Android 17, real disposable
  PostgreSQL/FastAPI, real I-012 node and Flutter/native tunnel; Telegram delivery
  only is TEST FAKE ONLY. Three independent leases, each at most 60s/1 MiB.
  Actual receipt facts: UI authentication; fresh native handshake and verified
  staging egress; background/foreground preserving the lease; Disconnect
  restoring ordinary network; authenticated API revoke stopping the VPN; lease
  expiry stopping the VPN; remote revoked/settled. No automatic renewal.
- Final cleanup passed all seven baseline checks (containers, filter/NAT,
  IPv6 filter, links, namespaces, IPv4 and IPv6 routes). Control grant removed,
  phone stopped and own ADB reverse removed, temporary key/profile removed;
  local API and disposable PostgreSQL stopped, temporary cluster removed.
  `i-013-staging.json` records these results at 2026-10-21T13:35:28Z.
  The controller's initial `egress_verified=false` belongs to endpoint startup;
  phone egress is separately measured as `flutter_handshake_and_staging_egress=true`.

Initial local physical cleanup failed because Flutter removed the APK before an
app-op reset; this was fixed with final-state checks and independent reverse
cleanup. Failure receipt retained as `i-013-local-android-cleanup-failure.json`.
A Connect/status-refresh race was fixed and covered by a regression test.

First approved I-013 staging attempt on 2026-10-20 stopped before Flutter login:
the device's arm64-only build conflicted with lockfile presence of x86_64 engine
artifacts. It was reproduced without VPS, corrected by SDK-pinned Flutter engine
coordinates outside presence locking, and verified with an arm64 build plus an
actual local physical/API rerun (`passed=true`). Before the next grant, runner
now compiles the exact physical test target. Failure cleanup/grant removal passed;
receipt retained as `i-013-staging-failure-first.json`. The approved rerun on
2026-10-21 passed as recorded above; no further staging change is required.

Credential-free UI previews are generated by `test/ui_preview_test.dart` from
actual widgets (Roboto/Material fonts from pinned SDK), with disconnected state
and unknown quota. These PNGs are UI renders, not screenshots of a live tunnel.

Test coverage includes permission cancellation/refusal, double connect, delayed
POST cancellation, ambiguous create replay with the same UUID, remote revoke,
native DOWN/expired lease, unavailable API without renewal, quota rejection,
stale cached integer bytes, redirect denial, concurrent refresh and logout during
refresh. These tests do not prove an actual VPN connection or production safety.

## Final ordinary APK completion — 2026-10-21

The remaining build failure in `.cache/i-013/final-lock-completion.txt` was a
debug-generated Java registrant referencing `IntegrationTestPlugin` while the
release dependency graph correctly excluded that dev plugin. In pinned Flutter
3.47.7, `--no-pub` also skips native plugin regeneration. The build script now
runs debug unit/lint separately, then `flutter build apk --release` without
`--no-pub`, with explicit `lib/main.dart` and the same optional API origin.
It enforces the pub lockfile first and rejects export if its SHA256 changes.
The client's app Gradle lockfile now includes the SDK dev plugin's debug runtime,
unit and instrumentation-lint configurations. Locking remains enabled; the
standalone I-011 tunnel lockfile was preserved byte-for-byte.

Final `scripts/build_flutter_client.ps1` completed from a fresh source snapshot:
format (0 changes), analyze (no issues), Flutter tests (22 passed), debug APK,
native unit tests (10 passed), lint (0 errors / 1 Gradle patch warning), unsigned
release APK. PowerShell AST parse passed. Local build output is retained in
`.cache/i-013/final-build-completion.txt`. Tooling warnings about future
Gradle/AGP/Kotlin support and the debug dev plugin's higher NDK remain; no
compatibility checks or lint errors were suppressed.

Fresh APK audit: `docs/evidence/i-013-apk-audit.json`. Both artifacts contain only
arm64-v8a/x86_64 native libraries, with AWG core for each ABI; backup is disabled
and scanned test-route markers are absent. Debug includes the SDK's native dev
plugin but uses ordinary `main.dart`; release excludes that plugin and the debug
network policy, with cleartext disabled. The audit's manifest parser was adjusted
for aapt2's `=false` output instead of the older `(type 0x12)0x0` spelling.
This remains a bounded artifact scan, not a complete supply-chain/secret audit.

`apksigner verify --verbose` passed for the debug APK (v2 signature). It was
installed on Pixel 8 Pro / Android 17 and opened via the ordinary launcher
activity. Installed APK SHA256 matches the exported artifact; the process is
alive, the activity is resumed, and UI hierarchy confirms the ordinary
"API не настроен" setup screen. Receipt: `docs/evidence/i-013-final-app.json`.
No API origin was embedded, no VPN connection was attempted and no staging/VPS
run was repeated. The app was left open on the phone. The successful staging
receipt remains unchanged; I-014 was not started.

## Known limitations and remaining release gates

Wi-Fi/mobile transitions and DNS/IPv6 leak checks were not measured for this APK;
previous I-011 measurements are not re-labelled as new evidence. Real Telegram
delivery and a publicly deployed control API were not exercised. Sessions and
device keys remain ephemeral; account/device recovery and full history are
outside I-013. Production, public distribution, licences,
SBOM/supply-chain audit and security review remain separate gates.

No migrations or public API contract changes. No production infrastructure was
changed. Approved staging changes were temporary and cleanup was verified.
No I-014/history work was included.

## Files and next action

Implementation: `apps/client/**`; one module-relative SDK path correction in
`apps/android/tunnel/build.gradle.kts`; `.gitignore`. Reproduction:
`scripts/install_flutter.ps1`, `scripts/build_flutter_client.ps1`,
`scripts/run_i013_android.ps1`, `services/api/tests/test_i013_android.py`.
Docs: issue/ADR-025/runbook/this report/ROADMAP; sanitized receipts in docs/evidence.

Recommended next issue is I-014 (full quota/history), requiring its own assignment.

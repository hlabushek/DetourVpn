# ADR-107: In-app update from our own server (Android)

Status: IMPLEMENTED IN CODE (run 2), NOT VERIFIED ON A DEVICE. Backend manifest, decision rules,
release script (run 1) plus the Android check, dialog, download, verification and installer
hand-off (run 2). Manifest signing is still open (decision 5).

## Context
Android updates are currently an APK distributed through Telegram. The owner wants the app to
check our server and offer the latest build. Existing code: no version or update endpoint existed
(grep of `services/api` for version/manifest: none).

## Decisions
1. **One latest release.** A single manifest file on the server, read on each request
   (`GET /api/v1/app/update-manifest`). Fields: `version_code`, `version_name`,
   `min_supported_version_code`, `apk_url` (HTTPS only), `apk_sha256` (64 lowercase hex),
   `apk_size_bytes`, `release_notes_ru`. Unknown fields are rejected.
2. **Missing or invalid manifest = unavailable (404 `update_unavailable`).** Never a partial
   or guessed value. Details of validation errors are not echoed.
3. **Blocking vs optional** (`UpdatePolicy.decide`):
   - installed < minimum supported → BLOCKING (the dialog cannot be dismissed; the owner decides
     the minimum, the app only enforces it);
   - installed < latest but >= minimum → OPTIONAL (dismissible);
   - installed >= latest → NONE. Older manifests never produce an update (no downgrade).
4. **Integrity before install:** the APK is downloaded over HTTPS only, its SHA-256 is compared
   with `apk_sha256` (exact lowercase match), and only then handed to the system installer.
   Redirects to non-HTTPS are refused by the planned client.
5. **Manifest signing: done in code (run 4, 2026-11-11); waiting for the owner's key.**
   - File format: `app-update.json` is an envelope `{payload, signature, key_id}`. `payload` is
     the exact manifest JSON bytes in standard base64. `signature` is Ed25519 over those bytes.
     `key_id` is the first 16 hex characters of SHA-256 of the raw public key. Signing exact bytes
     avoids any canonical-JSON rule shared between Python, Dart and Java.
   - Public key: `UPDATE_SIGNING_PUBLIC_KEY` (API, `app_update.py`) and
     `UpdateSignature.PUBLIC_KEY_B64` (Android). A test fails if they differ. **Both are empty
     today**, so nothing is served and the app offers no update until the owner runs
     `scripts/app_update_keygen.py --private-key-out <secure path outside the repo>`, pastes the
     printed public key into both constants and rebuilds. The private key is passphrase-encrypted
     PKCS#8 PEM. It is never in the repo, and no file written by these sessions holds it.
   - Release: `scripts/prepare_app_update.py --signing-key <path>` reads the key from the
     owner's location (passphrase asked in the terminal), refuses a key inside the repository,
     and refuses to write when the signature does not verify with the app's public key.
   - API: the file is served only if its signature verifies with the configured key (default:
     the repo key). The new `GET /api/v1/app/signed-update-manifest` returns the envelope, and the
     old `GET /api/v1/app/update-manifest` returns the verified manifest. An unsigned (old-format)
     file is unavailable.
   - App: Dart fetches the envelope and asks the native side to verify it (`appUpdateVerify`).
     Only the verified payload is parsed. At install, Java verifies again and takes URL, SHA-256,
     size and versions from the verified payload only (`appUpdateInstall`); Dart values are not
     used. A refusal shows «Не удалось проверить подпись обновления. Обновление не установлено.»
   - Limit: Android provides Ed25519 in `java.security` from API 33 (Android 13). On Android 7–12
     the app cannot verify, so it offers **no** in-app update (fail closed). Those users update
     from the Telegram APK as before. No crypto library was added and no primitive was
     hand-written (AGENTS.md).
   - Tests: Python `test_app_update.py` (valid, signed-but-invalid, unsigned, tampered payload,
     flipped signature, wrong key, relabelled key id, no key, endpoints, Java/API key equality,
     release script incl. refusals, keygen refusals): 25 passed with `test_health.py`. Java
     `UpdateSignatureTest` (valid, tampered, wrong key, malformed) 5 passed on JDK 21. Dart
     `app_update_test.dart` 13 passed. Not run on a phone.
6. **Release tooling:** `scripts/prepare_app_update.py` computes the SHA-256 and size, validates
   the result with the same model the API uses, and writes `app-update.json`. It uploads nothing.

## Android side (run 2)
- **Check on start.** `lib/app_update.dart` `checkForUpdate` runs once per launch from
  `main.dart` (Android only; Windows builds pass no updater). It reads the installed
  `versionCode` from the native side (`appVersionCode`) and calls the public manifest endpoint.
  Any failure (404 `update_unavailable`, network, invalid field) means no dialog. No retry loop.
- **Dialog (Russian).** Blocking: "Нужно обновить приложение", only «Обновить», back and tap
  outside are ignored (`PopScope(canPop: false)`, `barrierDismissible: false`). Optional:
  "Доступно обновление" with «Позже». `release_notes_ru` is shown as plain text.
- **Download.** `AppUpdateInstaller.java`, own single-thread executor (not the VPN worker), one
  download at a time. HTTPS only (`UpdatePolicy.isAcceptableUrl`), redirects disabled, 10 s
  connect / 20 s read timeouts, no cache. Target: app-private `cacheDir/updates/update.apk`.
- **Verification before install, in order:** declared and actual size must equal
  `apk_size_bytes` (download stops as soon as it exceeds it); SHA-256 must equal `apk_sha256`;
  `PackageManager.getPackageArchiveInfo` must report our own package name and exactly the
  manifest `version_code`. Any failure deletes the file.
- **Downgrade rejection, three layers:** Dart `decideUpdate`, Java `UpdatePolicy.decide` before
  downloading (installed >= manifest → "not_newer"), and the archive version check above.
  Android itself also refuses a lower `versionCode` and a different signing key.
- **Hand-off.** `ACTION_VIEW` with a `content://<applicationId>.updates/update.apk` URI and a
  one-off read grant. The file is removed on the next cold start (it must stay readable while
  the system installer is open).
- **FileProvider.** The app has no androidx dependency, and adding `androidx.core` would change
  the locked dependency set. `UpdateApkProvider.java` is a minimal replacement: not exported,
  `grantUriPermissions="true"`, read-only, serves exactly one path (`/update.apk`), no insert,
  update, delete or listing.
- **`REQUEST_INSTALL_PACKAGES`.** Declared in `AndroidManifest.xml` only so the verified APK can be
  handed to the system package installer. It does not allow silent installs: Android still asks
  the user to allow "install unknown apps" for DetourVPN (the app opens that settings screen and
  says so in Russian) and to confirm each install. Google Play restricts this permission; this
  build is distributed outside Play. If the app ever moves to Play, the updater must be removed
  or replaced by Play's in-app update API.
- **Outcome codes** returned to Dart: `installer_opened`, `install_permission_required`,
  `not_newer`, `invalid_manifest`, `busy`, `download_failed`, `size_mismatch`, `hash_mismatch`,
  `package_mismatch`, `installer_unavailable`. Each maps to a Russian message.

## Verification
- `services/api/tests/test_app_update.py`: 24 tests incl. the endpoint (passed with
  `tests/test_health.py`). Real PostgreSQL not needed.
- `UpdatePolicy.java` decision, HTTPS and SHA-256 logic: compiled and run in a javac harness
  (all checks passed). JUnit file `UpdatePolicyTest.java` written, not run through Gradle.
- `scripts/prepare_app_update.py`: smoke-tested on a dummy file.
- OpenAPI regenerated (`packages/contracts/openapi.json`). NOTE: the file was already stale before
  this change, so the regenerated file also contains schema changes from the owner's uncommitted
  API work. Review that diff before committing.

## Verification (run 2)
- `flutter analyze` (whole client, ASCII copy of the sources): no issues.
- `flutter test` `app_update_test.dart` (+ dns, primary action, widget, ui preview): 29 passed.
- `javac -Xlint:all --release 17` against Android SDK 36: `AppUpdateInstaller`,
  `UpdateApkProvider`, `UpdatePolicy`, `DnsChoice` compile with no warnings.
- JUnit 4 `UpdatePolicyTest` + `DnsChoiceTest`: 11 passed (run outside Gradle).
- Full `scripts/build_flutter_client.ps1` run (exit 0): all Flutter tests (170), Gradle unit
  tests (55, incl. `UpdatePolicyTest`), Android lint, debug and (unsigned) release APK. This
  compiled `ClientActivity.java`; `aapt2` shows the permission and provider in the APK.

## Known limitations
- No manifest signing (decision 5). No device test: download, the unknown-sources screen,
  the system installer and the cleanup on next start are unverified.
- The download is cancelled if the app process dies; the user simply taps «Обновить» again.
- Owner must upload the APK and manifest manually to the path the API serves. The API setting
  is `VPN_APP_UPDATE_MANIFEST_FILE` (path to the JSON). See the runbook.

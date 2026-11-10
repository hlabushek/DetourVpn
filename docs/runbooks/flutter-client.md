# Flutter Android client — I-013

Scope and limitations: ADR-025; acceptance: `docs/issues/I-013.md`.
The installed application ID of the beta client is `com.detourvpn.client`; the old I-011
harness is not replaced. The internal Android namespace remains `org.example.vpnclient`.
You must not keep another VPN active while measuring this test tunnel.

## Local build

Requires Windows/PowerShell, the Android Studio JDK, Android SDK 36/build-tools 36,
and the upstream GoBackend-only native libraries from the `android-poc.md` runbook.
Flutter 3.47.7 (BSD-3-Clause), revision/archive SHA256 are pinned in
`apps/client/toolchain.json`; Dart dependencies are pinned in `pubspec.lock` (hashes).
The app and tunnel dependency graphs of the new Flutter host are pinned in
`android/app/gradle.lockfile` and `android/tunnel-gradle.lockfile`; the standalone
I-011 lockfile is not changed by the Flutter build.
Flutter engine ABI artifacts are pinned by the engine hash from the SDK, not by a requirement
that all ABIs be present in the lockfile: the physical Pixel is built for arm64 only.
A regular debug build also includes the SDK's standard native plugin `integration_test`
(a dev dependency); its dependencies are pinned in the debug lock configurations.
Release excludes the dev plugin; this is additionally verified by the APK audit.
Gradle 8.14.3 (Apache-2.0) and Kotlin build plugin 2.4.21 (Apache-2.0) are pinned.
Kotlin 2.4.21 replaces the affected build plugin 2.2.20 (GHSA-r937-wjx7-w2jp);
compatibility with AGP 8.13.0 and Gradle 8.14.3 is confirmed by the official matrix.
The pinned Flutter SDK 3.47.7 still uses KGP 2.2.20 in the internal
Gradle project of the Flutter tools; do not patch the SDK in place. Until a verified
upstream stable SDK update, this build-tool advisory remains open.
AGP 8.13.0 was chosen for compatibility with the verified native module; Flutter emits
warnings about the future end of support for these versions. The SDK requirement
check is not disabled; moving to AGP 9 is a separate, compatible tooling update.

```powershell
.\scripts\install_flutter.ps1
.\scripts\build_flutter_client.ps1
```

The script creates a fresh snapshot of the allowed sources in
`%LOCALAPPDATA%\ProjectVpn\i013-builds\<random-id>`: client, native app sources,
tunnel build/lockfile and the prepared SDK. Private API/staging configuration,
local.properties and old build trees are not copied. Checks run there;
regular APKs and reports are returned to the original `apps/client/build/app`.
This eliminates the Dart LSP crash on Cyrillic and the Dart stat() failure on OneDrive cloud reparse
points. Edit the sources only in the main checkout; the local
snapshot is kept for diagnostics and is not a working source project.
No keys or sessions are needed for this build. NDK 27.0.12077973 may be
installed by the Android Gradle tooling if it is missing. No PATH/system
environment settings are persisted. The build installs dependencies locally,
runs analyze/tests and the Android unit/lint/release checks. An unsigned release APK
is not a public release; signing/publication are not performed at this stage.
The 2026-10-29 check showed that the release variant was not signed; the last
owner beta install was a debug APK. Gradle now supports a separate beta key,
supplied only via environment variables of the build process. If not all
four variables are set, release signing is not configured; with a partial set,
Gradle fails, listing only the names of the missing variables. The standard
build script requires an existing keystore at an absolute path outside the checkout.
Gradle forbids enabling release signing while the application ID is the
temporary placeholder `org.example.vpnclient`.
Configuration and verification are described in ADR-066 and
`docs/evidence/i-030-beta-signing-config-20261029.json`.

Before building a signed beta APK, the owner must create/select, outside the repository, the
beta signing key and arrange closed storage and backup recovery of the
key. `com.detourvpn.client` is the permanent beta ID;
changing it after the first distribution would break update compatibility. Do not send the
key, passwords or variables containing them to chat and do not write them into the repository.
Before handing the APK to testers, verify the signature, application ID,
version, SHA-256 and update continuity from the previous beta version.

`scripts/build_beta_apk.ps1` performs a release build with this key and always
enables the v3 window-tunnel API/controller; a signed beta APK must not call
the closed v2 tunnel-admission endpoint. For old local v2 scenarios,
use the lower-level `scripts/build_flutter_client.ps1` without `-WindowTunnels`.
On first
run the script offers to create a JKS in `%LOCALAPPDATA%\ProjectVpn\beta-signing`
and continues only after a local `CREATE` is entered. `keytool` asks for the
JKS password and the key password in the terminal; you do not need to enter the path. Choose passwords of
at least 16 characters and keep them with the owner. When the key password is requested, Enter
reuses the keystore password. Before the build, the script asks for
both passwords again in hidden fields, passes them temporarily only to the build process and
clears the variables after the APK is verified. The script does not install onto a device or hand
the APK over.
The key and passwords must be stored separately in an owner-controlled backup;
the local directory by itself is not a backup.

### Backup of the signing JKS on a BitLocker USB drive

For `GERFFINSUSB (E:)`, first finish BitLocker To Go and make sure the drive
is unlocked. Save the BitLocker unlock password and recovery key in a
password manager; keep both JKS passwords there as separate secrets. Do not
put the recovery key only on the encrypted USB drive and do not pass the values to chat.

From a PowerShell launched with elevated rights under the same JetGame
account, run:

```powershell
.\scripts\backup_beta_signing_keystore.ps1 -DriveLetter E:
```

The script checks the label and removable drive type, requires fully completed
BitLocker encryption and enabled protection, refuses to overwrite a finished
or leftover partial copy, and compares the SHA-256 of the source JKS with the copy.
A successful output `code=beta_signing_keystore_backup_verified` confirms an exact
copy on the encrypted drive; it does not confirm that the BitLocker recovery key
or the JKS passwords have been saved. Keep the JKS itself and any backup keys outside the checkout.

### Verifying restoration of the signing JKS

Before the check, confirm locally in the password manager that the unlock password
and the BitLocker recovery key, as well as both JKS passwords, are saved there. Do not
copy or send the values anywhere. Unlock `GERFFINSUSB (E:)` and run from a
regular PowerShell under the owner; the test copy is created in the already protected
directory `%LOCALAPPDATA%\ProjectVpn\beta-signing` outside the checkout:

```powershell
$restoreCopy = Join-Path $env:LOCALAPPDATA ('ProjectVpn\beta-signing\detourvpn-beta-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.jks')
$backup = 'E:\DetourVpn-Beta-Backup\detourvpn-beta-signing.jks'
if (Test-Path -LiteralPath $restoreCopy) { throw 'Restore-check destination already exists.' }
Copy-Item -LiteralPath $backup -Destination $restoreCopy
$sourceHash = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash
$restoreHash = (Get-FileHash -LiteralPath $restoreCopy -Algorithm SHA256).Hash
if ($sourceHash -ne $restoreHash) { throw 'Restore-copy SHA-256 mismatch.' }
'restore_copy_sha256=verified'
.\scripts\build_beta_apk.ps1 -KeystorePath $restoreCopy -ApiOrigin 'https://193.68.88.239'
```

The build asks for both JKS passwords in hidden fields and signs the local beta APK with the restored
copy. Before cleaning up the test copy, compare the fingerprint in the new receipt with the previous
beta receipt. After a successful comparison, delete only the file at `$restoreCopy`; do not touch the original
JKS and the backup on the USB drive. If the build or comparison failed, keep the
test copy until the cause is found. This check does not replace the natural run of the
daily backup task and does not prove that the BitLocker recovery key is stored in the
password manager.

```powershell
.\scripts\build_beta_apk.ps1 -ApiOrigin 'https://YOUR_APPROVED_API_HOST'
```

For the beta build, `-ApiOrigin` is required. The script accepts only an HTTPS origin
without credentials, path, query or fragment and passes it to the debug and release
builds as `VPN_API_ORIGIN`.

To verify an already signed APK locally, create a separate receipt:

```powershell
uv run --frozen python scripts/verify_beta_apk.py `
  --apk apps/client/build/app/outputs/flutter-apk/app-release.apk `
  --expected-application-id com.detourvpn.client `
  --output docs/evidence/i-030-beta-apk-20261029.json `
  --sdk-root "$env:LOCALAPPDATA\Android\Sdk" `
  --java-home 'C:\Program Files\Android\Android Studio\jbr'
```

The owner chose `com.detourvpn.client`. For each subsequent receipt specify a
new path so as not to overwrite earlier evidence. The receipt contains the ID,
version, APK SHA-256 and certificate fingerprint, but not the APK path. The script
uses `aapt` and `apksigner` from the Android
SDK/JDK without calling a Windows batch wrapper, does not install the APK and leaves `update_install: not_verified` and
`distribution: not_accepted`. Physical installation over the previous beta version
and the actual hand-off to testers must be accepted separately.

Debug and release are built sequentially via `flutter build apk` with an explicit
`--target lib/main.dart`. Between them Gradle runs only the debug unit/lint.
Do not combine debug and `:app:assembleRelease` in a single direct Gradle call:
Flutter must regenerate the native plugin registrant for release,
excluding `integration_test`. Otherwise javac receives a reference to the missing dev plugin.
In the pinned SDK, `--no-pub` also skips this regeneration, so release
is run without it after `pub get --enforce-lockfile`; before export the script
checks that the SHA256 of `pubspec.lock` has not changed.
`-ApiOrigin`, if set, applies to both builds.

By default the native AAR includes arm64-v8a and x86_64. If the Flutter target is
only `android-arm64`, an arm64-only AAR is built for the physical-test APK, which
reduces the compiler's temporary disk use. Gradle selects the AAR by
`target-platform`; multi-ABI builds continue to use the previous
two-ABI AAR.

APK: `apps/client/build/app/outputs/flutter-apk/app-debug.apk`.
To install a regular debug APK after the build and open it on the only
connected Android phone:

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r apps/client/build/app/outputs/flutter-apk/app-debug.apk
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" shell am start -W -n com.detourvpn.client/org.example.vpnpoc.ClientActivity
```

Without an API origin the app honestly shows a setup screen. For a real API:

```powershell
.\scripts\build_flutter_client.ps1 -ApiOrigin 'https://YOUR_APPROVED_API_HOST'
```

Do not include tokens/credentials in the origin or dart-define. Release allows only
HTTPS with the system certificate verification. Local debug-only transport:
`http://127.0.0.1:18766` via ADB reverse; plaintext remote hosts are forbidden.
The Android network security config exception exists only in the debug source set.

## Diagnostics by consent

On a connection error, the button «Поделиться диагностикой» ("Share diagnostics") opens a preview.
Check the code and stage; cancel closes the dialog without calling the Android share API.
After confirmation, Android shows the system Sharesheet, and the text goes
only to the app chosen by the user. The report contains a fixed error code
and stage; unknown values are replaced with `unknown_error`/`unknown`.
The account, keys, addresses, app list, DNS, traffic history and tunnel contents
are not included. There is no automatic sending and no crash reporting.

A permanent support contact is not yet configured. The tester chooses the recipient
independently; before sending, check the preview and do not add secrets,
a profile, an OTP or user traffic to it. Physical verification of the
Sharesheet and the instructions on the beta APK remains open.

## Phone checks without an external endpoint

Connect and unlock exactly one phone, allow USB debugging.
For the permission cancellation test the app must not yet have VPN approval:

```powershell
.\scripts\build_flutter_client.ps1 -DeviceChecks
.\scripts\run_i013_android.ps1
```

The first test uses the real upstream core/key/channel: native DOWN,
invalid profile/proof/TTL rejection, cancellation of an unfinished system permission.
No tunnel to an external endpoint is created. The second test uses a disposable real
PostgreSQL + loopback FastAPI + the real Flutter UI/native. The Telegram sender is
**TEST FAKE ONLY**, the code is passed via a test-only loopback route; the user's
Telegram and the production API are not affected. The native permission is granted in advance
via app-op only for this test package and is returned to default after the run.
The login UI, device/key proof, admission closed and the absence of a false
connected are verified; the API tunnel intent is deleted, the app is stopped, the reverse
is removed, API/PostgreSQL are stopped and the temporary DB is deleted.
Sanitized receipt: `docs/evidence/i-013-local-android.json`.

Integration entrypoints are located only in integration_test, test server routes
only in the pytest module. They are not included in the regular main.dart/release API.
After the integration test, rebuild the regular APK: `build_flutter_client.ps1`.
Do not distribute an APK built with a test entrypoint. Raw Flutter failure output
is kept only in the runner's memory and is not shown, because widget diagnostics
may contain the login code. The receipt contains only fixed flags/stage.

## New bounded staging run — separate permission

I-013 was already technically accepted on 2026-10-21: its own physical Flutter receipt
`docs/evidence/i-013-staging.json`, `passed=true`; 1 passed / 391 deselected.
Below is the procedure for reproducing a new run. Repeating staging is not required
to view the interface. Real Telegram delivery, DNS/IPv6 and a Wi-Fi/mobile
switch have not been verified for this APK; release gates remain open.

Requires explicit consent for one bounded run on `45.10.166.106` within the
unchanged ADR-024 controller. Do not change existing VPNs/containers/routes.
Endpoint and agent up to 600s, expiring grant up to 45m; 3 independent client leases up to
60s/1 MiB, without extension. Production operations are not covered by the permission.

After consent, the user runs locally (password only in the Ansible prompt):

```powershell
.\scripts\run_i013_android.ps1 -Staging
```

For an already issued fresh grant, after checking the cause of the previous failure:
`-Staging -UseExistingGrant`. Do not blindly repeat a failed run.
Do not send the password, SSH key, profiles or access/refresh/code to chat.

The runner uses the verified I-012 controller without a new remote shell surface.
Before the sudo/grant request, a local compilation of the exact integration test
entry point and a check of the connected phone's ABI are performed; on error the VPS is not touched.
The API stays loopback, the agent reaches it via SSH reverse, Android via ADB
reverse. The header protection profile is stored outside the checkout under a user-only ACL.
Telegram delivery remains TEST FAKE; real auth/API/node/Android/egress are actual.
Scenarios: UI login → create → node ACK → native handshake → staging egress;
background/foreground; Disconnect → revoked/settled → network restore;
server revoke → UI/native DOWN; new lease → expiry → local DOWN/settlement.
The receipt `docs/evidence/i-013-staging.json` does not replace raw assumptions:
`passed=true` is permissible only after all facts and cleanup.

Cleanup: remote expire/seven baseline checks + grant removal; SSH transport close;
stop phone/reset own app-op/remove reverse; secret profile/key removal; stop local
API/PostgreSQL. The runner leaves no active peer or permanently running API.
An unsuccessful cleanup must be resolved before a new staging run.

## Manual acceptance checklist

- Denial of the Android VPN permission: no connected and no real native UP.
- Connect/Disconnect changes the system VPN; native UP without a handshake is not connected.
- Wi-Fi/mobile loss and return: reconnecting, without an automatic new peer;
  the state is recomputed when the screen returns. Do not switch the user's radios
  automatically before an agreed test.
- Verify real IPv4/IPv6/DNS and the return of the normal network; a fresh handshake
  alone does not prove the absence of leaks. The I-011 sample does not replace a new APK test.
- Theme/font scaling/screen reader; token/key/profile are not shown/not logged.
- On API failure, cached quota carries a staleness mark; bytes are always integer.

Known limitations: ephemeral key/session, manual new sign-in after process death,
device-limit cleanup via the existing device API/account tooling; one staging
endpoint; no auto renewal/region selection/full history. After the session is lost,
one can sign in again to complete a pending revoke; the intent remains only in RAM.
On account change/process death, the server kernel lease remains the last gate;
the client does not promise a confirmed remote revoke without a node ACK.
Public release requires licence/supply-chain/SBOM/security/privacy gates.

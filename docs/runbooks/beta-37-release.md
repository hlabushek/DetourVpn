# Runbook — release 0.1.0-beta.37 (code 37)

Owner-only steps. Everything here needs the owner's hands: keystore and key passwords, the update
signing passphrase, the test phone, the upload. Passwords are typed only into the scripts' hidden
prompts; never paste them anywhere else. Changelog: `docs/releases/beta-37-changelog.md`.

State before you start (prepared without secrets):
- Branch `claude/connection-reliability`; version already bumped to `0.1.0-beta.37+37`
  (`apps/client/pubspec.yaml`) and code `37` / `0.1.0-beta.37` (`build.gradle.kts`), committed.
- Nothing signed, built for release, installed, deployed or published.
- The kill switch ships **opt-in, off by default** (ADR-196). Do not change that for this release.

## 0. Script to use

There is **no `release_app_beta37.ps1`**. The closest script is `scripts/release_app_beta34.ps1`
(two stages: signed APK + phone install, then `-PublishOnly`). It has its version constants hard
coded, so make a local copy and change only these lines:

```powershell
Copy-Item .\scripts\release_app_beta34.ps1 .\scripts\release_app_beta37.ps1
```

In `scripts\release_app_beta37.ps1` change:

| Line (in beta34) | From | To |
|---|---|---|
| `$VersionName` | `'0.1.0-beta.34'` | `'0.1.0-beta.37'` |
| `$VersionCode` | `35` | `37` |
| `$OldPubspec` | `'0.1.0-beta.33+34'` | `'0.1.0-beta.36+36'` |
| `$OldVersionName` | `'0.1.0-beta.33'` | `'0.1.0-beta.36'` |
| `$OldVersionCode` | `34` | `36` |
| `$OutDir` | `'ProjectVpn\updates\beta34'` | `'ProjectVpn\updates\beta37'` |
| `$notes` text | beta.34 text | your Russian release note (suggested below) |
| publish hint (`release_app_beta34.ps1 -PublishOnly`) | `beta34` | `beta37` |

Suggested `$notes` text: `"Обновление DetourVpn ${VersionName}: стабильнее подключение, понятнее
главный экран, обновление с прогрессом загрузки; на Windows — автоматическое восстановление и
настройка «Блокировать трафик при обрыве» (по умолчанию выключена)."`

The bump is already in git, so stage 1 prints "Version already 0.1.0-beta.37+37: bump skipped".
Keep the copy uncommitted (or commit it yourself); untracked files do not block the preflight.
`$WindowSeconds` stays `3600` (ADR-150). The script passes no `-ApiFallbackOrigins`, as before.

Existing parameters of the copy (unchanged from beta34): `-MinSupportedVersionCode` (default 1,
nobody is forced), `-NotesFile`, `-SkipVersionBump`, `-SkipBuild`, `-NoInstall`, `-PublishOnly`.

## 1. Android — signed APK and phone install (stage 1)

1. Plug in the test phone (USB debugging on). If an emulator is also attached, use `-NoInstall`
   and install by serial (`adb -s <serial> install -r <apk>`), as the script header says.
2. Run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\release_app_beta37.ps1
   ```
3. Type the **keystore password** and the **key password** into the hidden prompts.
4. Note the printed `apk_sha256`. The APK copy is under `%LOCALAPPDATA%\ProjectVpn\updates\beta37`.
5. The script installs over the existing app (`adb install -r`, keeps data) and stops.

## 2. Phone test (before anything reaches testers)

Use the tester checklist in the changelog. Minimum: connect; stay connected through a 3600 s
window; switch Wi-Fi ↔ LTE; check the home-screen states and the navigation bar colour. Send the
diagnostic report if anything fails. Record pass/fail only; no IPs or identifiers.
If the phone test fails, **stop**: do not publish.

## 3. Windows — unsigned zip

1. Build (no password prompts; nothing is signed or published):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_client.ps1 -BuildEngine
   ```
   `-BuildEngine` rebuilds `detour-engine.exe` from the pinned source (as `release_beta.ps1
   -Windows` does). `-ApiOrigin` defaults to the beta origin; `-ApiFallbackOrigins` defaults to
   empty. The script runs its built-in format/analyze/test and C++ test gates.
2. The zip is `apps\client\build\windows-zip\DetourVpn-windows-x64-0.1.0-beta.37.zip`; the script
   prints `zip_sha256=` and `unsigned=true`.
3. Optional receipt:
   ```powershell
   uv run --frozen python scripts/verify_windows_zip.py --zip apps\client\build\windows-zip\DetourVpn-windows-x64-0.1.0-beta.37.zip --expected-version 0.1.0-beta.37 --output docs\evidence\i-032-beta-windows-<yyyyMMddTHHmmss>.json
   ```
4. Before sending it to testers, run the ADR-196 leak test on a real Windows PC with the setting
   **on** (at least steps 1, 2, 4, 5, 9). The setting stays off by default whatever the result.
5. Distribute the zip **together with its SHA-256**, and say it is unsigned (UAC and SmartScreen
   warnings are expected).

## 4. Publish the in-app update (stage 2, only after the phone test passed)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\release_app_beta37.ps1 -PublishOnly
```

1. Type the **update signing passphrase** into the hidden prompt (manifest signing).
2. Read the dry-run output of the upload.
3. Type `YES` only if it looks right; anything else cancels with no server change.
4. Phones on a build with the update public key (Android 13+) are offered 0.1.0-beta.37.
   Older Android versions need the APK installed by hand.

## 5. After release

- Post the tester text from the changelog (Russian section) with the Windows zip SHA-256.
- Record sanitized pass/fail receipts in `docs/evidence/` (no IPs, keys or traffic).
- Not part of beta.37: tray mode (beta.38, after the leak test), dead-peer probe (needs a
  project-owned HTTPS health endpoint from you), Windows code signing.

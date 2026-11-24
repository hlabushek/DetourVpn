# ADR-193 — In-app update banner, progress sheet and explicit install (refines ADR-107, ADR-188)

**Status:** accepted. Code uncommitted. **Not built into an APK, not run on a phone.**

## Context
Owner request: the update should happen inside the app, not through Android's notification
shade. When the server has a newer version, a banner should slide in at the top of the app.
Tapping it should show a clear download progress bar with a Cancel button, then a smooth move
to installation.

What existed (ADR-107, ADR-188): an automatic check opened a modal dialog for every newer build.
A static banner existed on the VPN tab only. Native downloaded over HTTPS, verified the file and
**opened the system installer on its own** as soon as the file verified. Progress came from
polling native byte counts every 300 ms. Cancel worked. The bytes were written straight to the
file name the `UpdateApkProvider` serves.

## Decision
1. **Banner, app-wide.** `UpdateBannerSlot` (`lib/update_ui.dart`) sits under the app bar on
   every screen (VPN, Traffic, Profile, sign-in). It slides and fades in when a check finds a
   newer build and out when hidden. The banner shows the version and a status line: size, live
   «12,3 МБ из 45,6 МБ · 27 %» with a thin bar, «Проверка файла…» or «Файл скачан и проверен».
   Tapping the banner or its button opens the sheet.
2. **Dismissal.** An optional build's banner has a close button; it stays hidden for the life
   of the process for that version code (`UpdateScheduler.dismissBanner`). A newer build shows
   again. A **blocking** build has no close button and cannot be hidden.
3. **When the sheet opens by itself.** An automatic check opens the sheet only for a blocking
   build. An optional build is announced only by the banner. A manual check in Settings still
   opens the sheet.
4. **Sheet.** `showUpdateSheet` is a modal bottom sheet with the version, the size and
   `release_notes_ru` («Что нового»). It shows one panel per stage, with animated changes:
   - Determinate bar driven by native byte counts, with «Отменить загрузку».
   - Indeterminate bar «Проверка файла…» (cancel disabled).
   - Ready: a check mark and the primary button «Установить».
   - «Открываем установщик Android…» while the hand-off runs.
   - Exact outcome texts for size, SHA-256, package/version, signature, invalid manifest, network
     failure and cancel. «Повторить загрузку» appears only where a new download can help; a
     blocking sheet always keeps a way forward. A blocking sheet cannot be dragged, tapped away
     or closed with back.
5. **Explicit install.** `appUpdateInstall` takes `openInstaller` (absent means true).
   `UpdateDownload.start` calls it with `false`: native downloads and verifies, then answers
   `verified` without opening anything. «Установить» calls it with `true`. Native runs every
   check again on the cached file (ADR-188 reuse path) and opens the installer. The «install
   unknown apps» permission is now asked at «Установить», not before the download.
6. **State machine.** `UpdateStage`: `idle`, `downloading`, `verifying`, `ready`, `installing`,
   `cancelled`, `failed`. «Offered» is `UpdateScheduler.bannerOffer` holding an offer while the
   stage is `idle`. The stage after a native answer comes from one table
   (`UpdateDownloadState.stageAfter`). `install` is ignored outside `ready`; `start` is ignored
   while a call runs.
7. **Partial file.** Native writes the bytes to `cache/updates/update-partial.apk`, a name the
   provider never serves. Only after the exact size, SHA-256, package name and version checks
   pass is it renamed to `update.apk`. On cancel or failure both files are deleted. The copy
   loop moved to `UpdateDownloadFile` (no Android types) so plain JUnit can test it.

## Security
- Every ADR-107/188 check is kept, unchanged and native:
  - the manifest is verified with Ed25519 (`UpdateSignature`) at check and again at every
    install call;
  - all install values come from the verified payload; Dart supplies no URL or hash;
  - HTTPS only, no redirects, size cap and exact size, SHA-256, own package name, exact
    version code and no downgrade.
- An unverified or partial file is never at the served path. `install` re-verifies before every
  hand-off.
- No new permission, no new exported component, no storage permission. The provider still
  serves one read-only path with a one-off grant. Progress exposes only phase, byte counts and an
  outcome code. Nothing new is logged.
- Progress comes from real bytes (native `RECEIVED`), never from a timer that invents values.
  The 300 ms poll only reads them.

## Alternatives rejected
- **Dart-side download (`HttpClient`)**: it would move hashing and size checks into a second
  implementation and need a file hand-off to native anyway. Native already had progress and
  cancel.
- **EventChannel**: same reasons as ADR-188.
- **Banner overlaying the app bar**: it would hide the Settings button for a blocking build.

## Known limitations
- If the process dies, the download and the "ready" marker are lost; the next start deletes
  leftovers (ADR-188).
- If the server publishes a newer build while a verified file for the older one is ready, the
  sheet still shows «Установить». Native then sees a different SHA-256 and downloads the new
  file, with progress, before the installer opens.
- Size labels use MiB with a comma («0,5 МБ»).

## Verification
- `flutter test`: 416 passed, 54 of them in the two update test files. `flutter analyze` on an
  ASCII copy: no issues. `dart format`: clean.
- Java: `UpdateDownloadFileTest` (8), `UpdatePolicyTest` and `UpdateSignatureTest` passed, 17 in
  total. They were compiled with javac `--release 17` against android-36 and run with JUnit
  4.13.2. This was a reduced compile of the update classes only, not the full HANDOVER
  substitute; `ClientActivity.java` was not compiled.
- Not verified:
  - APK build;
  - download on a phone, on Wi-Fi or mobile data;
  - cancel mid-download;
  - the «install unknown apps» screen and the system installer prompt;
  - behavior across Android versions (13+ only, because of Ed25519);
  - the banner animation on real screens.

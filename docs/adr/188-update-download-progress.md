# ADR-188 — Update download progress, cancel and re-install (refines ADR-107)

**Status:** accepted. Code in the working tree, uncommitted. **Not built into an APK, not run on a
phone.**

## Context
Owner report: while the in-app update APK downloads there is no progress (no percent, no bytes).
Tapping «Обновить» again shows «Загрузка уже идёт.» and nothing else; only after the whole file
arrives does Android's installer appear.

What the code did (ADR-107): `appUpdateInstall` was one long method-channel call. Native downloaded,
verified (exact size, SHA-256, package name, version code) and opened the system installer, then
answered one outcome code. The dialog kept a local `busy` flag. An optional dialog can be dismissed
while busy (tap outside / back); the next dialog is a fresh widget that knows nothing of the running
call, so its «Скачать» reached native, which answered `busy`. If the user closed the system
installer, the verified file could only be reached by downloading it again.

## Decision
1. **Native progress by polling.** `AppUpdateInstaller` keeps in-memory, process-wide state for the
   one download: phase (`idle`, `downloading`, `verifying`, `ready`), bytes received, expected total
   (the size from the signed manifest), and the last outcome code. New channel method
   `appUpdateProgress` returns only those four values. Dart polls every 300 ms while a download runs.
2. **Cancel.** New channel method `appUpdateCancel` sets a flag checked in the read loop and closes
   the connection from a short-lived daemon thread, so a stalled read ends at once. The download
   answers `cancelled`; the partial file is deleted. A cancel wins even over a file that just
   verified.
3. **One app-wide download in Dart.** `UpdateDownload` (owned by `UpdateScheduler`) holds the
   state as a `ValueNotifier`; every update dialog listens to it. Reopening the dialog (banner,
   Settings, automatic check) shows the running download; «Скачать» is not offered while it runs.
   If native still answers `busy` (a recreated activity whose Dart side lost the call), Dart follows
   the native run by polling and reports its last outcome instead of a dead message.
4. **Dialog.** Determinate `LinearProgressIndicator` with «12,3 МБ из 45,6 МБ · 27 %» while bytes
   arrive; indeterminate bar with «Проверка файла…» while the file is checked; «Отменить загрузку»
   while downloading; «Скрыть» (optional updates only) closes the dialog and the download continues.
   Clear outcome texts for failures, cancel and success.
5. **Install again without a new download.** After a verified file, the primary button becomes
   «Установить». It calls `appUpdateInstall` again; native reuses the cached file only when the
   signed manifest has the same SHA-256 and version as the file it verified, and only after running
   every check again (size, SHA-256, package, version, not a downgrade). Otherwise it downloads anew.
   The installer still opens automatically after a successful download, as before.
6. **Start-up cleanup.** A fresh activity no longer deletes the update file while this process is
   still downloading it or holds a verified one (`discardStaleAtStart`). A new process starts with
   no verified state, so its activity deletes any leftover file, as before.
7. The download's busy flag is now released after the failure cleanup, so a new download cannot
   start while the previous attempt deletes its file.

## Alternatives rejected
- **EventChannel stream for progress:** more plumbing (stream lifecycle across activity recreation)
  for no user-visible gain over a 300 ms poll of four numbers.
- **Android DownloadManager:** writes outside app-private storage or needs extra handling, follows
  redirects on its own (ADR-107 forbids redirects) and gives less control over TLS and size limits.
- **Foreground notification with progress:** useful, but a new notification channel and permission
  surface; out of scope here.
- **Trusting Content-Length for the total:** the signed manifest size is the authoritative total;
  a different Content-Length already fails as `size_mismatch`.

## Security
- Verification is unchanged and still all native: HTTPS only, no redirects, exact size, SHA-256
  from the Ed25519-signed manifest, package name and version code, no downgrade. Dart only relays
  the signed envelope; it never supplies a URL or hash.
- A reused file is re-verified in full before every hand-off to the installer; the "ready" marker
  lives only in memory, so it never survives a process restart.
- The file stays in app-private cache (`cache/updates/update.apk`), served only through the
  existing `UpdateApkProvider` with a one-shot read grant. No new exported component.
- The progress call exposes phase, byte counts and an outcome code only: no URL, hash, path or
  server identity. Nothing new is logged.
- Android still requires the same signing key and the user's confirmation in the system installer.

## Known limitations
- The installer opens automatically after download; if the app is in the background, Android may
  block that launch silently. The dialog then says to tap «Установить», which works on return.
- The home-screen banner does not show progress (it reopens the dialog, which does).
- If the process dies mid-download, the download is lost; the next start deletes the partial file.

## Verification
See the report of this change; on-device behavior is not verified.

# ADR-098 — Android VPN service restore: sticky service, boot restore, Always-on in release

**Status:** proposed 2026-11-11 (step C3 of the owner-approved fault-tolerance plan).
**Amends:** ADR-040 (Android window lifecycle) and ADR-042 (Always-on was enabled only in debug).
**Builds on:** ADR-096 (long windows), ADR-097 (native renewal).

## Context

The patched GoBackend service returned `START_NOT_STICKY`. If Android killed the app process
under memory pressure or through an OEM battery manager, the VPN stayed off until the user opened
the app. There was no boot restore. The Always-on manifest flag was `false` in release, so users
could not turn on the system "Always-on VPN" either. The foreground notification still said
"VPN I-011 · test service" and used a warning icon.

## Decision

1. **Sticky service.** `GoBackend.VpnService.onStartCommand` returns `START_STICKY`.
   - A restart after the system kills the process arrives with a null intent and goes through the
     existing ADR-042 restore callback (`alwaysOnTriggered`). That callback reads the saved
     session, opens the protected API relay, starts the headless Dart controller and admits a new
     window.
   - A stop through `stopSelf()` (user disconnect, window expiry, restore failure) is not
     restarted, so a crash loop or a wanted stop never comes back.
2. **Explicit restore action.** `VpnService.RESTORE_ACTION` from the app is treated like an
   Always-on start.
3. **Boot restore.** `BootReceiver` reacts only to the protected `BOOT_COMPLETED` broadcast, which
   other apps cannot send. It starts the service with `RESTORE_ACTION` only if both:
   - `resume_connected` is set. It is set after a successful `connectWindow` and cleared by any
     app-side disconnect, including logout or a failed window.
   - VPN consent is still ours (`VpnService.prepare() == null`).

   A duplicate start next to system Always-on is ignored by the restore path.
4. **Revocation is a wanted stop.** If the service is destroyed after consent moved to another VPN
   app, or the user revoked it, the app clears `resume_connected`, stops native renewal and reports
   `vpn_revoked`.
5. **Always-on in release.** `SUPPORTS_ALWAYS_ON=true` for release builds. Lockdown keeps failing
   closed as in ADR-042.
6. **Notification.**
   - Channel `detourvpn.vpn`, named "VPN-подключение". The old `i011` channel is deleted.
   - Text "DetourVPN включён / Нажмите, чтобы открыть приложение", with a monochrome
     `ic_stat_vpn` icon (fallback: the platform lock icon).
   - Tapping it opens the app.

## SDK patch provenance

- `apps/android/patches/go-only.patch` now has hash
  `caaab24d8b7d09aeaa6d44ceacdd361e1e150b6d11697972b5dd1600148c5d50`, pinned in
  `apps/android/tunnel/build.gradle.kts`. Only the `GoBackend.java` section changed. The
  `SharedLibraryLoader` and `api-android.go` sections are byte-identical.
- The prepared tree `.cache/i-011/sdk-go-only-c3-20261111` was produced by the logic of
  `scripts/prepare_android_poc.py`. Only the repository root and the patch path differ, because in
  a worktree `.cache` links to the main checkout. Comparing its receipt with
  `sdk-go-only-beta-v3-20261026` shows only `GoBackend.java` changed, and every Go source is
  identical. On that basis its `jniLibs` (`libwg-go.so`) and license `assets` were copied unchanged
  and hash-verified.

## Limits / not verified
- Not verified on a physical device: OEM battery managers can still block sticky restarts or boot
  broadcasts.
- The restore path admits a new window and uses Dart-driven renewal in a headless engine. It does
  not use ADR-097 native renewal yet. Under Lockdown the native HTTPS client would need
  `VpnService.protect()`, which is not implemented.
- There is no "Отключить" action in the notification yet.
- A restore after a process kill creates a new connection. The old one closes at its window
  deadline.

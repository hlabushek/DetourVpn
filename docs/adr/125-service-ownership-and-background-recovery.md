# ADR-125 — Foreground service ownership and background recovery

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2).

## Facts (verified in code)
- The VPN runs in `org.amnezia.awg.backend.GoBackend$VpnService` (our patched copy): foreground
  from `onCreate` (`startForeground`, type `systemExempted`, channel «VPN-подключение»),
  `START_STICKY`, `onDestroy` turns the device off and reports `alwaysOnStopped`. Target SDK 36.
  The manifest declares `FOREGROUND_SERVICE` and `FOREGROUND_SERVICE_SYSTEM_EXEMPTED`;
  `systemExempted` is the documented type for VPN apps.
- The Flutter Activity never owns the engine; closing it does not stop the service. A
  `FlutterActivity` engine dies with the Activity (ADR-117).
- `onRevoke` is not overridden: Android's default calls `onDestroy` via `stopSelf`, which reports
  `alwaysOnStopped`; `ClientApplication` then sees `VpnService.prepare() != null` and records
  `vpn_revoked` (terminal, intent cleared).
- `onTaskRemoved` is irrelevant: the service is not bound to the task; swiping from Recents only
  ends the Activity and its engine.

## Decision
1. Keep the AWG service as the only foreground service; no second service class.
2. `ClientActivity` reports its engine's attach/detach (`configureFlutterEngine`,
   `cleanUpFlutterEngine`) to `ClientApplication` (`uiAttached`). Detach is **not** a disconnect.
3. When the supervisor (ADR-124) says "recover" and no Dart owner is attached, native starts the
   service with `RESTORE_ACTION` (`startForegroundService`). The service's start command runs the
   existing restore path (headless Dart engine, protected API relay, fresh window).
   Background start limits (Android 12+): allowed while the app is exempt from battery
   optimizations (the app asks for that at first connect), from the boot broadcast, or while the
   app is visible. Our recovery alarm is inexact (`setAndAllowWhileIdle`), which does **not** by
   itself grant the exemption (only exact alarms do, and those need a permission this app should
   not request), so in practice the battery exemption is what makes background recovery start. A refused start (`ForegroundServiceStartNotAllowedException`, a
   `RuntimeException` subclass) is caught and recorded as `background_start_refused`; the next
   trigger (network change, alarm, app open, boot) tries again.
4. Triggers that re-evaluate the supervisor: a native stop, the physical network becoming usable,
   the recovery alarm (`setAndAllowWhileIdle`, same not-exported receiver as renewal), a restore
   attempt ending, the UI engine detaching.
5. The restore engine reports a failure code with `bootstrapFailed {code}`; terminal codes clear
   the intent (no retry storm on `session_expired` or quota).

## Platform limits (not claimable)
- A force-stop from Settings cancels alarms and stops everything until the user opens the app.
- OEM battery managers may kill the process or deny background starts despite the exemption.
- After the service stops (window ended, offline), the process may be killed; then only an
  alarm, the boot receiver or the user can resume.

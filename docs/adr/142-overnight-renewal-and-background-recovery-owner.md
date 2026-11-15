# ADR-142 — Overnight renewal stop with the screen off, and who reconnects while the app is in the background

**Status:** accepted by the owner 2026-11-15; decisions 1–3 implemented on branch
`claude/connection-reliability` (see "Implementation"). **Not verified on a device.**
**Amends:** ADR-125 (who owns reconnects: the Activity lifecycle, `onStart`/`onStop`, replaces
"engine attached"), ADR-136 (the recovery owner switches with that lifecycle; still one at a time),
ADR-140 (renewal timing for 900 s windows). Separate from ADR-141 (false Always-on hint).

## Evidence (owner's Pixel 8 Pro, beta.33, 2026-11-14/15; server times UTC)
Owner and server reports, not reproduced here:
- Android always-on set for the app, Lockdown off; phone not rebooted, app not updated.
- Server windows for the phone: a short window 23:23:01–23:24:31; a long-window connection
  23:24:58–01:13:02, renewed normally, then **closed by expiry** (not revoked by the server);
  **no Android connection 01:13–07:51**; a new connection at 07:51:24 after the owner opened the app.
- In the morning Android showed «Нет подключения к постоянной VPN»; no VPN network agent existed.

Not available yet: the phone's diagnostic report (`failureStage`, stop reason, `nativeRenewal`,
`recoveryNote`, `uiAttached`, device-idle line). Without it, which of the causes below fired first
is **inferred, not proven**.

## Analysis (verified in code at this branch)

### A. Why native renewal could miss the 01:13 deadline
Beta builds use 900 s windows. `NativeRenewal.renewBeforeMs()` = max(55 s, 900 − 300 s) = **600 s**,
so after a renewal native sleeps ~294 s, then renews with ~600 s left. The deadline itself is
`intentAt + duration − 6 s` (`NativeRenewal.adopt`).

Every wake-up in Doze comes from `setAndAllowWhileIdle` (`NativeRenewer.schedule`); the in-process
`io.schedule` and the worker's `deadlineCheck` do not run while the CPU sleeps. Android defers
allow-while-idle alarms in deep Doze (historically at most one per app about every 9 min), and the
app's three such alarms (renewal, recovery `RECOVER`, restore `WAKE_RESTORE`) share that allowance.

So in deep Doze one renewal can look like this:
1. The due alarm (planned at +294 s) fires up to ~9 min late, leaving roughly 60 s of window.
2. The wake-up does a burst of at most 4 attempts within 30 s (`burstDelayMs`), never past
   `remaining − 6 s`. A transient failure is plausible right after Doze: the radio and DNS are
   waking, or the node has not yet acknowledged the renewal (`202`, then up to 40 s of polling).
3. The retry alarm (`retry` → `backoffMs`, capped to `remaining − 6 s`) is again an
   allow-while-idle alarm, so Doze can push it past the deadline. The window ends: `lease_expired`.

The margin therefore covers **one** deferred alarm and no failed attempt.

**Caveat (added at implementation).** How far Doze defers allow-while-idle alarms depends on the
platform version and target SDK (the app targets SDK 36): older releases used a fixed minimum gap
of about 9 min per app, newer ones use a per-app quota. Neither model was measured on the owner's
phone. If the gap is counted from the app's previous allow-while-idle alarm rather than from the
planned time, renewing earlier does not move the first deferred wake-up; it then only adds retry
time after a failed burst (decision 2 test: ≥ 12 min instead of about 9.5 min). Decision 1 is the
part that does not depend on this model. The 1 h 48 min of
successful renewals followed by a stop fits the phone entering deep Doze some time after the screen
went off. A silent stop is also possible: on 401/403/404/409/422 (for example `renew_token_invalid`)
`NativeRenewer.finish` stops renewal for good, and the window then simply expires. The diagnostic
report (`failureStage = native_renewal`) and the server's request records around 00:58–01:13 would
tell these apart.

### B. Why nothing reconnected for 6.5 hours
1. **The UI engine blocks native recovery while the app is merely in the background.**
   `ClientActivity.configureFlutterEngine` sets `uiAttached = true` (`ClientActivity.java:71`) and
   it is cleared only in `cleanUpFlutterEngine`, i.e. when the Activity is destroyed. Screen-off or
   pressing Home only stops the Activity, so it stays `true` all night.
   `ClientApplication.evaluateRecovery` passes it as `dartOwner`, and `ConnectionSupervisor.evaluate`
   returns `NONE / dart_owner` (`ConnectionSupervisor.java:153`). Native's alarm-driven recovery,
   the only path that can run in Doze, never starts.
2. **The Dart controller that "owns" recovery cannot run in Doze.** Its auto-reconnect is
   `Future.delayed` (`window_connection.dart` `_scheduleAutoReconnect`, `_scheduleResume`) and its
   poll is `Timer.periodic`. Neither wakes the CPU. After the stop, `stop()` releases the CPU lock
   and the foreground service ends, so the app is now a background process; Doze also blocks its
   network until a maintenance window. A Dart attempt in a maintenance window would most likely
   fail with `network_unavailable` and back off again on a timer that sleeps.
3. **Android's Always-on does not restart the app when the app stops its own VPN.** The platform
   starts the Always-on app on boot or unlock, on a settings change and on package replace. When
   the app tears its VPN down (`provider.disconnect` → service `onDestroy`), Android only shows the
   «Нет подключения к постоянной VPN» notification. (AOSP behaviour as understood; not verified on
   this device.)
4. When the owner opened the app, the UI controller reconciled and connected (07:51:24).

If the app had been swiped away (`uiAttached = false`), native recovery would have tried. That path
has its own limit: starting a foreground service from the background may be refused on Android 12+
(`background_start_refused`, ADR-134); it then retries at most once a minute, on Doze-limited alarms.

## Decision (proposed)
1. **Background ownership: native owns recovery whenever the Activity is not started.**
   - Native tracks `uiForeground` from `ClientActivity.onStart` / `onStop` (true only between them)
     in addition to `uiAttached`. `evaluateRecovery` passes `dartOwner = uiForeground ||
     recoveryEngine != null`. `onStart` and `onStop` each re-run `evaluateRecovery`.
   - The backgrounded Dart controller must not race it: its auto-reconnect and resume timers do
     nothing while the app lifecycle is not `resumed` (Flutter `AppLifecycleState`, already observed
     in `main.dart`). On `resumed`, it reconciles first: a tunnel brought up by the restore engine is
     adopted through the existing hand-over (ADR-121, `RestoreHandover`, `detachRestoreForUi`); if
     the restore engine is still connecting (`restoreActive` and not up), Dart waits for it and does
     not call `disconnect`.
   - Native renewal is unchanged in ownership: once a window is up, native renews (ADR-140), and
     Dart only observes.
2. **Renewal margin for 900 s windows.** Renew earlier, so one deferred alarm plus a failed burst
   still leaves another alarm before expiry: `renewBeforeMs = max(55 s, window − 120 s)` (renew about
   2 min after each grant; Dart's `_renewBefore` changed to match). The cost is about 7 renewal calls
   per connected phone per 15 min instead of 3; server load and quota accounting are otherwise
   unchanged (windows still reserve their budget, ADR-130). No server change.
3. **Recovery start after expiry.** When the window expires with the intent still wanted, native
   schedules its first recovery from the same wake-up that saw the expiry, so it does not wait for
   another deferred alarm.
4. No change to the server, window length, quota or reservations. A longer window remains the
   ADR-140 follow-up and needs a server ADR.

## Implementation (2026-11-15)
- **Ownership (decision 1).** `ClientApplication.uiForeground`, set by `ClientActivity.onStart` /
  `onStop` (cleared when the UI engine detaches); each change re-runs `evaluateRecovery`.
  `evaluateRecovery` passes `ConnectionSupervisor.dartOwnsRecovery(uiForeground, recoveryEngine
  != null)`. `uiForeground` is added to the snapshot for diagnostics.
- **Dart gating (decision 1).** `WindowConnectionController(nativeOwnsBackgroundRecovery: true)`
  only for the Android UI controller (`main.dart`; off on Windows and in the headless restore
  engine). `appForegroundChanged` comes from `didChangeAppLifecycleState`: foreground =
  `resumed` or `inactive` (shade, dialogs; the Activity is still started), background =
  `hidden`/`paused`/`detached`, which matches `onStop`. In the background the auto-reconnect and
  resume timers keep their attempt as deferred instead of connecting; on return to the
  foreground a controller that holds no connection marks itself unsynced and runs the deferred
  attempt, whose `connect` reconciles first.
- **Restore still connecting.** `_reconcile` returns early, without `disconnect` and without
  syncing, while the snapshot says `restoreActive` and not `up`; the UI shows «Восстанавливается
  подключение» and polls. Once up, the existing ADR-121 adoption takes it over. If the restore
  ends without a tunnel and native no longer wants the VPN, the UI leaves the reconnecting state
  (`disconnected`), so no stuck spinner.
- **Margin (decision 2).** `NativeRenewal.RENEW_AFTER_GRANT_MS = 120 s`,
  `renewBeforeMs = max(55 s, window − 120 s)`; Dart `_renewBefore` the same. This also applies to
  the Windows client's Dart renewal (more renew calls, no behaviour change otherwise).
- **Immediate first recovery (decision 3).** `ConnectionSupervisor.tunnelDown(reason, now)`: a stop
  after at least `STABLE_MS` (10 min) up clears the attempt history, so `evaluate` returns
  `START_RECOVERY` in the same wake-up. `setDeadline` marks the start of the up period. A tunnel
  that was up only briefly keeps its backoff.
- **Remaining platform limit.** The recovery start is `startForegroundService` from the
  background; Android 12+ may refuse it (`background_start_refused`, ADR-134), then it is retried
  at most once a minute on allow-while-idle alarms.

## Verification (2026-11-15, local only)
- JVM (`javac --release 17` + JUnit 4.13.2): `ConnectionSupervisorTest` (ownership switch,
  immediate recovery after a stable window, short up keeps backoff), `NativeRenewalTest` (new
  margin, retry room after a failed burst), `RenewalOwnerTest`, `AlwaysOnStatusTest`: 46 passed.
- `scripts/build_flutter_client.ps1 -WindowTunnels -WindowSeconds 900 -TargetPlatforms
  android-arm64` (beta API origin): dart format clean, `flutter analyze` no issues, `flutter test`
  286 passed (4 new ADR-142 controller tests), debug APK built, `:app:testDebugUnitTest` 164 passed,
  `:app:lintDebug` passed, release APK built (debug-signed, not for distribution).
- Not done: install, physical device, overnight run, Doze measurements.

## Trade-off against ADR-136 (single owner)
ADR-136 avoids two actors creating competing windows for one device. This proposal keeps one owner
at any moment but makes the owner depend on the Activity lifecycle: **native (restore engine)
while the Activity is stopped, Dart UI while it is started**. The risk sits at the switch:
- Dart wakes (`resumed`) while the restore engine is mid-connect → mitigated by "wait, don't
  disconnect" in decision 1 and by the server refusing a second live connection.
- The restore engine is started just as the user brings the app forward → the existing ADR-121
  hand-over covers a running tunnel; a start that has not finished yet is the remaining race, and
  the reason for a JVM/Dart test of both orderings.
- Rejected alternative: keep Dart as owner and hold a partial wake lock while disconnected-but-wanted.
  It keeps one owner but drains the battery all night and still has no network in Doze.
- Rejected alternative: exact alarms (`setExactAndAllowWhileIdle`). They need the user-granted
  `SCHEDULE_EXACT_ALARM` on Android 12+, and Doze still limits allow-while-idle alarms.

## Security and privacy
No new endpoint, permission, credential or data. Renewal still uses the connection-scoped renew
token only. Faster renewal adds API calls but no new data. Recovery stays bounded by the existing
backoff (30 s … 5 min, none while offline).

## Not claimable
- That deep Doze, a failed renewal attempt or a refused renewal caused the 01:13 stop. All three
  fit the server timeline; the phone report decides.
- OEM battery managers, force-stop, revoked VPN permission and Lockdown limits (ADR-139/140) are
  unchanged.

## Real-device checks required (owner's phone, build with this ADR implemented)
Record pass/fail, time of any drop, the status text and the diagnostic code only.
1. Before the fix, from beta.33: send the diagnostic report from this morning's state if still
   available. Run `adb shell dumpsys deviceidle whitelist` and check that the app is on it
   (battery «Без ограничений»).
2. **Overnight, app in background (Home, not swiped), screen off, ≥ 6 h, part of it off the
   charger.** Pass: no gap in the server's connection windows longer than the recovery backoff,
   and the key icon is present in the morning.
3. **Forced expiry with the screen off:** airplane mode on for 20 min with the screen off, then off.
   Pass: the VPN returns by itself without opening the app (native recovery while backgrounded).
4. **Hand-over race:** with the VPN down and native recovery in progress, open the app. Pass: one
   connection on the server, no `revoke_unconfirmed` loop, and the screen ends in «VPN активен».
5. Repeat 2 with the app swiped away, to compare.
6. Measure deep-Doze entry: `adb shell dumpsys deviceidle` state during the night run (read-only).

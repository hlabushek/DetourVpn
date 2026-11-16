# ADR-143 — Fresh wake and network signals reset native recovery backoff

**Status:** accepted by the owner 2026-11-16; implemented on branch `claude/connection-reliability`
(see "Implementation"). **Not verified on a device.**
**Builds on:** ADR-142 (recovery owner follows the Activity lifecycle; immediate first recovery
after a stable window), ADR-140 (no attempt budget; network return skips backoff once),
ADR-139 (bounded recovery, now amended by ADR-140), ADR-134 (background start refusals).
**Amends (if accepted):** ADR-140 security note on retry rate (see "Disagreements").
Approach informed by WG Tunnel (MIT). Described in our own words; no code was copied
(source note: `docs/research/wg-tunnel-reconnect-comparison-20261116.md`, idea 1).

## Problem
After ADR-142 the native supervisor (`ConnectionSupervisor`) owns recovery while the app is in the
background, but it acts only when one of these runs: its own `setAndAllowWhileIdle` recovery alarm,
the default-network callback in `ClientApplication.watchNetwork`, the UI engine attaching or
starting, or a tunnel stop. Its backoff (30 s doubling to 5 min, ±25 %) is kept across all of them.
So after a failed night attempt, the phone can wake (Doze ends, the owner turns the screen on, a new
network appears) and native still waits for the next backoff alarm, which Doze may defer further.
The moment the device is most able to reconnect (awake, network up, maybe interactive) is not used.

## Decision (proposed)
1. **Signals.** A *fresh signal* is one of:
   - `wake`: the device leaves deep Doze (`PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED` and
     `isDeviceIdleMode() == false`), leaves light idle (API 33+:
     `ACTION_DEVICE_LIGHT_IDLE_MODE_CHANGED` and `isDeviceLightIdleMode() == false`), the screen
     turns on (`Intent.ACTION_SCREEN_ON`), or the user unlocks (`Intent.ACTION_USER_PRESENT`);
   - `network`: the default physical network changes identity or becomes usable (the existing
     `watchNetwork` callbacks: `onAvailable` with a different network, or `onCapabilitiesChanged` to
     usable). A loss is not a signal.
2. **Where it is registered.** One receiver, registered at runtime in
   `ClientApplication.onCreate` (Application scope, worker-thread handoff), next to
   `watchNetwork`. These actions are registered-only broadcasts; the manifest cannot declare them.
   Use `RECEIVER_NOT_EXPORTED` on API 33+ (protected system broadcasts are still delivered). No new
   permission. No service, wake lock, alarm or network call is added by the receiver itself.
3. **What a fresh signal does** (pure policy, new `ConnectionSupervisor.freshSignal(now, kind)`):
   - It acts only when all of these hold: intent desired, tunnel not up, last stop not terminal
     (`ConnectionSupervisor.terminal`), native owns recovery
     (`dartOwnsRecovery(uiForeground, restoreEngineAlive) == false`, ADR-142), physical network
     usable, and no recovery attempt is in flight.
   - Then it clears the backoff (`attempts = 0`, last attempt forgotten, a held
     `background_start_refused` wait cleared once) and marks **one** immediate attempt as allowed.
     `evaluateRecovery` then returns `START_RECOVERY` and the existing path runs (`startRecovery` →
     restore service → headless restore engine → new window from the API).
   - **Rate limit:** at most one fresh-signal attempt per `FRESH_GAP_MS = 30 s` (the first backoff
     step), whatever the number of signals. Signals inside the gap are coalesced into the next
     evaluation. A failed fresh attempt goes back to the normal backoff ladder from step 1.
   - **Settle:** a `wake` signal is evaluated after `WAKE_SETTLE_MS = 2 s`; a `network` signal keeps
     the existing settle and flap rule (`NETWORK_RETURN_GAP_MS`, ADR-140). The wake settle lets an
     unlock followed by opening the app reach `ClientActivity.onStart` first, so the UI (not
     native) takes the attempt when the owner opens the app at once.
   - While the tunnel is **up**, a fresh signal changes nothing in recovery. (Using it to pull a due
     native renewal forward is a possible follow-up, not part of this ADR.)
4. **No race with the Dart UI.** The signal never starts anything itself; it only feeds
   `evaluateRecovery`, which already refuses when a Dart controller owns recovery (UI started or
   restore engine alive, ADR-142) and when an attempt is in flight. If the owner opens the app while a
   signal-started restore is connecting, ADR-142's Dart side waits for that restore and adopts it
   (`_restorePending`), never disconnects it. The UI's own `resumed` refresh is unchanged.
5. **Diagnostics.** `recoveryNote` gets the codes `fresh_wake` / `fresh_network` when a signal led to
   an attempt, and `fresh_ignored_<reason>` (owner, offline, gap, terminal) when it did not. Codes
   only; no network names, addresses or timestamps beyond the existing ages.

## The limit this cannot remove: cached processes
Runtime receivers and network callbacks reach the app only while its process is alive and not
frozen. While the tunnel is up, the VPN foreground service keeps the process alive. **After the
tunnel stops** (the overnight case), the app is an ordinary background process: Android may cache
and freeze it, and on recent releases broadcasts to a cached app are queued and delivered only when
it leaves the cached state. A screen-on broadcast may then arrive late, merged, or only when the
recovery alarm wakes the process. So, alone, this ADR helps most in the period right after a stop
and whenever the process is still running; it does not guarantee a reaction to every wake. The
companion foreground service while the intent is wanted (research idea 2) is the change that keeps
the process reachable; it needs its own ADR. The existing recovery alarm stays as the backstop.

## Disagreements with earlier ADRs
- **ADR-140 security note** says native makes "at most one attempt per backoff (≥ 30 s, ≤ 5 min)".
  With this ADR the bound becomes: at most one attempt per 30 s while signals keep arriving, else the
  backoff. Worst case is one attempt every 30 s during repeated screen toggling or network flapping.
  A failed attempt still reserves no quota (ADR-099/130), and each attempt is one window request.
- **ADR-142** is the base and is not contradicted: its ownership rule gates every signal, and its
  "immediate first recovery after a stable window" stays. This ADR adds resets on later wakes, which
  ADR-142 left to the backoff.
- **ADR-139** (bounded recovery) is already amended by ADR-140; no budget is reintroduced.

## Alternatives considered
- **Keep alarms only.** Simple, but uses the least favourable moment (a deferred alarm) instead of
  the most favourable one (the device just woke).
- **Manifest receivers.** Not allowed for these actions since Android 8.
- **WorkManager on network constraint.** Deferred and batched in Doze; useful only as a backstop
  (research idea 6), not as a fast path.
- **Reset the backoff on every signal without a gap.** Rejected: a flapping network or screen
  toggling would start a request every few seconds.

## Security and privacy
No new permission, endpoint, credential or data. Receivers are not exported and read only the
action and the platform's idle/screen state. Recovery uses the existing restore path and token
handling (ADR-121/140). Rate bounded as above. No server change.

## Implementation (2026-11-16)
Native only; no Dart change was needed (the Dart side already waits for a restore, ADR-142).
- `ConnectionSupervisor.freshSignal(now, tunnelUp, dartOwner, networkUsable)` returns
  `START_RECOVERY` ("fresh"), `WAIT` with the time left in the 30 s gap ("gap"), or `NONE` with the
  reason (`up`, `not_desired`, `terminal`, `owner`, `offline`, `in_flight`). Applying it sets
  `attempts = 0` and forgets the last attempt, which also clears a held
  `background_start_refused` wait. "In flight" = an attempt started less than 60 s ago that has
  not reported back (`attemptStarted` opens it; tunnel up, tunnel down, failure or refusal close it).
- `ConnectionSupervisor.wakeSignal(...)`: screen on and unlock are wake signals; an idle-mode
  change is one only on leaving idle.
- `ClientApplication.watchWake()` registers the receiver in `onCreate` (`RECEIVER_NOT_EXPORTED` on
  API 33+). A wake signal takes a 5 s partial wake lock (timeout only), cancels any earlier pending
  wake and schedules `freshSignal("wake")` after 2 s on the worker thread.
- `watchNetwork`: a changed default network (`PhysicalNetwork.available` true) and a network that
  just became usable (`capabilities` true) call `freshSignal("network")` after the existing
  `evaluateRecovery`.
- `freshSignal` (worker thread) runs `enforceDeadline`, asks the supervisor, then runs
  `evaluateRecovery` on START; on WAIT it schedules one coalesced re-signal at the end of the gap.
- `recoveryNote` codes: `fresh_wake`, `fresh_network` when a signal started a restore;
  `fresh_ignored_<reason>` except for the normal `up`/`not_desired`. The note is in the native
  snapshot only; the consent-based diagnostic report does not include it yet.

## Tests
- JVM `ConnectionSupervisorTest` (6 new): a fresh signal resets a long backoff and recovers at
  once; rate limit (a signal inside 30 s gets WAIT with the time left, the next one after the gap
  starts); no new start while an attempt is in flight, until it is 60 s old; the gate (up, Dart
  owner via `dartOwnsRecovery`, offline, not desired, terminal); a refused background start is
  retried on a fresh signal; the wake mapping (entering idle is not a wake).
- Run 2026-11-16: `ConnectionSupervisorTest` 32 passed (JUnit 4.13.2, `javac --release 17`);
  `scripts/build_flutter_client.ps1 -WindowTunnels -WindowSeconds 900 -TargetPlatforms
  android-arm64`: analyze clean, `flutter test` 286 passed, debug APK built,
  `:app:testDebugUnitTest` 170 passed, `:app:lintDebug` passed.
- Not unit-tested: the 2 s settle against `onStart` and the receiver itself (Android timing and
  broadcast delivery); covered only by the device checks below.
- Visibility: `recoveryNote` is not in the diagnostic report, so the device checks use the key
  icon, the app status and the server's connection times.

## Real-device checks required (owner's Pixel 8 Pro; read-only `adb` is enough)
Record pass/fail, times, the status text and `recoveryNote`. No addresses or keys.
1. **Wake after a night stop.** Screen off; force a stop with airplane mode for 20 min, turn it off
   with the screen still off, leave 2–3 h. Turn the screen on without opening the app. Pass: the key
   icon returns within about a minute and `recoveryNote` says `fresh_wake`. Also record whether the
   app process was cached (`adb shell dumpsys activity processes`, read-only).
2. **Network change while down.** With the VPN down by expiry and the app in the background, switch
   from Wi-Fi to mobile data. Pass: an attempt starts at once (`fresh_network`).
3. **Signal spam.** With the VPN down, toggle the screen 10 times in one minute. Pass: at most two
   new connections on the server in that minute.
4. **Race with opening the app.** With the VPN down, unlock and open the app within 2 s. Pass: one
   new connection, the screen ends at «VPN активен», no stopped restore.
5. **Deep Doze exit.** During a night run, record `adb shell dumpsys deviceidle` state transitions
   (read-only) and whether `fresh_wake` follows a deep-idle exit when the process is cached.
6. **Background start limits.** Count `background_start_refused` after signal-started attempts.
7. **Battery.** Compare one night's battery drop with and without the change; the receiver adds no
   wake-ups by itself, so the difference should only come from extra recovery attempts.

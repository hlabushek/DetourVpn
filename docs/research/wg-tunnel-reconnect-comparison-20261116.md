# Android reconnect, renewal and always-on: DetourVPN vs WG Tunnel

Date: 2026-11-16. Status: research note, no code changed. Nothing here is device-verified.
Our side: branch `claude/connection-reliability` (ADR-139..142, uncommitted/unpushed beta.33 line).
Their side: GitHub sources only, read as text (no clone, no binaries): `wgtunnel/android` @ `7c278975`
and the library it depends on, `wgtunnel/core` @ `6806529b` (the VpnService and recovery live there,
not in the app repo). `joh-b/wgtunnel` resolves to the same project (MIT).

## 0. The one difference that matters most

WG Tunnel's tunnel has **no expiry**. A user config is a long-lived WireGuard/AmneziaWG peer; the app
never has to ask a server for a new access window. So it has no equivalent of our renewal problem
(ADR-097/140/142). Whatever it does in Doze only has to keep the *process and tunnel alive* and
re-establish after a *network* loss. Our overnight failure was a **lease expiring while nothing could
run**, which their design never faces. The comparison below is therefore about the recovery and
lifecycle layers, not about renewal. For renewal there is nothing to adopt; only a longer window on
our own server (ADR-140 follow-up) removes the Doze dependency.

## 1. Mechanisms, ours vs theirs

| Mechanism | DetourVPN (this branch) | WG Tunnel |
|---|---|---|
| Process that holds the tunnel | AWG `GoBackend` VpnService foreground service in app process; Flutter UI engine is optional | Core library runs a VpnService plus a separate **companion foreground service** that owns the persistent notification; app UI is a plain Compose app with no engine to lose |
| Restart after process death | Boot receiver, 4-min `setAndAllowWhileIdle` restore alarm, headless Flutter "restore engine" (ADR-121) | Companion service is `START_STICKY`; when the system restarts it (null/foreign intent) it fires a "sticky restart" callback and the app re-starts the last active tunnel set from storage. The VpnService itself is deliberately `START_NOT_STICKY` |
| Boot / update restore | `BootReceiver` | One receiver for boot, vendor quick-boot actions and `MY_PACKAGE_REPLACED`; a startup policy chooses auto-tunnel, default tunnel or nothing; package-replace restores the last active tunnels |
| Always-on trigger | `alwaysOnTriggered` reads `isAlwaysOn()`/lockdown at start (ADR-139) and persists it; ADR-141 made the read valid only while established | Trigger = system start of the VpnService (service-interface action) **or** `isAlwaysOn`; fires a callback; app starts its default tunnel if the user enabled Always-on support in app settings. The app keeps its own switch and does not rely on the platform flag for anything but the trigger |
| Lockdown | Detected; native renewal refused under Lockdown because HTTPS sockets are unprotected (ADR-121/136) | Own "lockdown/kill switch" mode: a SOCKS bridge (hev-socks5-tunnel) keeps a local tunnel interface so traffic is blocked while the tunnel is down; its own sockets bypass the VPN via a socket protector |
| User stop vs system stop | Intent persisted; `userConnected/userDisconnected` bump a generation (ADR-124) | A `userActivatedShutdown` flag per service; on `onRevoke` the app marks "revoked this process" and **clears the last-active list**, so a sticky restart cannot silently resurrect a tunnel the user removed from Settings |
| Who decides to reconnect | One supervisor (`ConnectionSupervisor`), pure JVM, backoff 30 s..5 min, alarm-driven; owner switches Dart UI vs native by Activity start/stop (ADR-142) | Live reconciler in the running service: conflated triggers, one worker, a lock shared with user actions, bounded retries per input state, then "wait for inputs to change". Not alarm-driven |
| Recovery of a stuck tunnel | New window from the API after expiry/failure | Per-tunnel recovery loop on handshake failure: re-resolve endpoint, optionally "bounce" the whole tunnel; attempt budget **refills on every wake or network-change signal**; a short settle after a fresh signal |
| Network change handling | Default-network callback in `ClientApplication`, 10 s gap when a network returns (ADR-140) | Dedicated network-monitor module: default and per-transport callbacks, airplane-mode state, validated capability, debounce 300–500 ms on identity, and **asymmetric confirmation** (usable network believed at once, loss believed only after ~8–10 s) |
| Doze / screen off awareness | Wake lock bursts during renewal; `setAndAllowWhileIdle` alarms; no state observation | Observes `ACTION_DEVICE_IDLE_MODE_CHANGED`, light idle (API 33), screen on/off, user present; produces a monotonically increasing "wake epoch" fed to the recovery loop. No alarms, no wake locks |
| Periodic safety net | Alarms only | WorkManager periodic job (15 min) restarts the auto-tunnel service if its flag says it should be running |
| Battery exemption | Documented as an owner step | Declares the ignore-battery-optimizations permission and exposes the request |
| Background FGS start limits | Detected, `background_start_refused`, retry at most once a minute (ADR-134) | Uses special-use foreground service type; starts services from receivers and the WorkManager job; no special refusal handling seen in the files read |

## 2. Ideas worth adopting (own words, no code)

Ranked by value to the overnight failure. All need an ADR and a device test before they count.

1. **Treat "device woke up" and "network changed" as budget-refilling signals.**
   Source: `core` recovery loop (`features/TunnelRecovery.kt`) and power-state flow
   (`system/PowerManager.android.kt`). Behaviour: recovery attempts are not a fixed ladder; when the
   screen turns on, the device leaves Doze, or the default network changes, the backoff resets and a
   fast first attempt follows. Ours: after ADR-142 native recovery is alarm-driven only and keeps its
   backoff across wakes. A dynamically registered listener (needs a running service) for idle-mode-changed
   and screen-on that calls `evaluateRecovery` with a "fresh signal" flag would make recovery run when
   Doze ends, not at the next deferred alarm. Cheap, no server change.

2. **Companion foreground service that outlives a VPN teardown, restarts stickily, and triggers a
   restore.** Source: `core` `service/VpnCompanionService.kt` (sticky, restore callback) and
   `service/VpnService.kt` (non-sticky). Behaviour: the process Android resurrects is the
   foreground service, not the VpnService; the resurrected service asks the app to restore the last
   active tunnel. Ours has no foreground service once the tunnel stops, which is exactly the gap in
   ADR-142 section B2/B3 (after `lease_expired` the app is an ordinary background process). Keeping a
   low-importance foreground service while the intent is "wanted" would keep network and CPU access
   during Doze maintenance windows and keep the process alive. Risk: Android 14/15 foreground-service
   type rules and a permanent notification; must be a declared type with a real justification. Needs
   a decision (ADR) and battery measurement.

3. **Asymmetric network confirmation.** Source: `android` `service/autotunnel/AutoTunnelService.kt`
   (debounce on "usable network lost", ~8 s; gain trusted at once) and `networkmonitor` module.
   Behaviour: do not treat a brief validation flap as a lost network, but react to a return
   immediately. Maps to our `NETWORK_RETURN_GAP_MS` rule; worth adding the "loss must persist"
   half so we do not start recovery or stop renewal during Wi-Fi/LTE handovers.

4. **Revoke clears the restore list.** Source: `android` `core/orchestration/StartupCoordinator.kt`
   and the revoke callback in `core` `VpnService.onRevoke`. Behaviour: when the user removes the VPN
   from system settings, forget the "was running" record so a sticky restart or boot does not
   re-establish. Check ours: confirm `onRevoke` ends the intent (ADR-124 says intent changes only on
   explicit user action or terminal failure; revoke is a user action and should be named there).

5. **Reconciler discipline: one worker, conflated triggers, shared lock with user actions, stop
   after N stalled passes until inputs change.** Source: `core` `autotunnel/AutoTunnelReconciler.kt`.
   Behaviour: rapid events collapse to one pass against the newest state; a failing target is not
   retried until something changes. This matches our ADR-124 single supervisor; the new piece is the
   "inputs changed" release, which pairs with idea 1.

6. **Periodic WorkManager re-check (15 min).** Source: `android` `core/worker/ServiceWorker.kt`.
   Behaviour: if the service flag says "should run" and it is not running, start it. A second,
   OS-scheduled trigger that does not depend on our own alarms. Weak under Doze (minimum interval and
   deferral), so only a backstop; low cost.

7. **Own "Always-on support" setting separate from the platform flag.** Source: `android`
   `StartupCoordinator.handleAlwaysOnTrigger`. Behaviour: the app decides what to do on a system
   start from its own stored switch, so a wrong or unreadable platform answer (our ADR-141 bug class)
   cannot disable the feature. Related, not a fix for it.

Not worth adopting: their SOCKS-bridge kill switch (large and its licence was not checked, and our Lockdown
renewal issue is about protected sockets, not blocking); their auto-tunnel Wi-Fi rules (no
equivalent product need); Shizuku paths.

## 3. License check

| Source | License | Relied on for |
|---|---|---|
| `wgtunnel/android` (app) | MIT, copyright 2023-2025 Zane Schepke | startup/boot/sticky/revoke logic, worker, auto-tunnel service, manifest |
| `wgtunnel/core` (VpnService, recovery, power, reconciler) | MIT, copyright 2023-2026 Zane Schepke | all service-level behaviour in section 1 |
| `wgtunnel/wireguard-android`, `wgtunnel/amneziawg-android` | Apache-2.0 | named only (listed as the engine fork); not read for this note |
| `wgtunnel/amneziawg-go` | MIT | not read |
| `heiher/hev-socks5-tunnel` (submodule of `android`) | not checked here | only mentioned; not relied on |

Both read repositories were also confirmed MIT through the GitHub license metadata. No GPL source
was opened or used (Privycs and L x Box were not read).

If code (not just an idea) were ever ported: MIT requires keeping the copyright notice and the
permission notice in all copies or substantial portions. For our closed app that means a third-party
notices entry (name, "MIT", copyright line, full MIT text) in the app's open-source licences screen
and in `docs/licensing-checklist.md`, plus a comment at the ported file naming the source file and
commit. Ideas and behaviours described here are not copyrightable expression; this note contains no
code, and an independent implementation of sections 2.1–2.7 needs no notice. Because the engine fork
we already ship is Apache-2.0 derived (check `docs/patches` and the licences screen), its NOTICE
obligations are unaffected by this work. A short attribution line "approach informed by WG Tunnel
(MIT)" in the ADR that adopts an idea is good practice, not a legal need.

## 4. What cannot be known without a device test

- Whether a companion foreground service (idea 2) actually keeps network and CPU usable through deep
  Doze on the Pixel 8 Pro, and its battery cost over a night. WG Tunnel does not prove this for us:
  its tunnel is up and passing keepalives, ours is a lease that may be down.
- How far Android defers allow-while-idle alarms on this phone and target SDK 36 (ADR-142 caveat),
  and therefore whether idea 1 (recover on wake) beats our alarms at all. Needs
  `dumpsys deviceidle` and `dumpsys alarm` read-only during a night run.
- Whether idle-mode-changed and screen-on broadcasts reach an app that has no running foreground
  service (they are registered dynamically in WG Tunnel's service; manifest registration is not
  allowed for these).
- Whether a foreground service start from a Doze-woken receiver is accepted on Android 12+ here
  (`background_start_refused`), since WG Tunnel's behaviour in that case was not seen in the files
  read.
- What stopped the tunnel at 01:13 and why nothing restarted it: still needs the phone's diagnostic
  report (ADR-142 evidence list). WG Tunnel cannot settle that.
- Whether the platform Always-on flag read before `establish()` is wrong on this device (their start
  path also reads it, combined with the system start action; behaviour not observed).
- Anything about WG Tunnel's real-world reliability: no test was run, and its issue tracker was not
  reviewed.

## 5. Recommendation

The mature client does not offer a better *renewal* approach, because it has none to offer. For the
recovery layer, two changes look worth an ADR and a device test next: **recover on wake/network
signals** (idea 1) and a **foreground service that stays while the intent is wanted** (idea 2).
Everything else is polish. Replacing the tunnel engine would not change the overnight outcome.

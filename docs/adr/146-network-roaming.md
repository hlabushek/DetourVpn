# ADR-146 — Network roaming (connection-survivability Phase 4)

**Status:** implemented on branch `claude/connection-reliability` as Phase 4 of the owner-approved
connection-survivability plan; committed locally only, **not verified on a device**.
**Builds on:** ADR-099/122 (watchdog, pending network trigger), ADR-127 (physical network view,
restart in place), ADR-140 (no attempt budget, held for network), ADR-143 (fresh signals, 30 s
limit), ADR-144 (native state, single recovery owner).
**Supersedes in part:** ADR-122's 3 s settle and "three restarts per change, then the stall rule";
the watchdog's "ignore a change before the first tick" gate.

## Problem
- A default-network change (Wi-Fi ↔ mobile, loss and return) was acted on only after a 3 s settle,
  and a change reported before the first 15 s watch tick of a window was dropped
  (`TunnelWatchdog.networkChanged` returned while the watchdog was not yet "running").
- The VPN's underlying network was never set, so Android derived metering and validation of the
  VPN network on its own and not from the path the tunnel socket actually uses.
- Any `onLost` marked a running tunnel `recovering/waiting_for_network` at once, so a normal
  sub-second handover flashed «Восстановление связи».
- A change whose restarts never produced a handshake fell back to the 180 s stall rule, keeping a
  dead window for minutes instead of asking for a new one.
- A network that returned after the phone was offline started one attempt, but the backoff from
  failures before the loss stayed (up to 5 min to the next try on the new path).

## Decision
1. **Act on a new network after 0.5 s (4.1, 4.2).** `TunnelWatchdog.SETTLE_MS` is 500 ms. A
   confirmed change schedules a watch check at 0.55 s, which restarts the socket in place
   (`restartInPlace`) at once instead of on the next tick. The 15 s gap between restarts (ADR-122)
   stays; a change inside it is postponed, not dropped. The first-tick gate is removed:
   `networkChanged(now, tunnelUpSince)` starts tracking a running tunnel that the tick has not
   seen yet; with no tunnel (`-1`) it is ignored as before.
2. **Underlying network (4.1).** On a new default network, after every window start and after
   every restart in place, native calls `VpnService.setUnderlyingNetworks(new Network[]{n})` with
   the current default physical network (only while the tunnel is up and the network is usable).
   **Order deviation from the plan:** the plan says set it, then restart. A restart calls
   `establish()` again, and by our reading of AOSP `Vpn.establish()` the new configuration starts
   with the builder's (empty) underlying networks, so a value set before the restart would be lost.
   Native therefore restarts first and sets the network right after. Not verified on a device.
3. **Asymmetric loss confirmation (4.2).** `onLost` makes the network unusable at once (no
   restart, no recovery attempt runs offline) and arms an 8 s timer
   (`PhysicalNetwork.LOSS_CONFIRM_MS`). While the loss is unconfirmed, a running tunnel is not
   marked `recovering/waiting_for_network` for the loss alone (`ConnectionState.Facts
   .networkLossPending`); its other rules (fresh handshake, stall, pending restart) still apply.
   A new `onAvailable` cancels the timer. A down-but-wanted tunnel still shows
   `waiting_for_network` at once.
4. **Backoff (4.3).** A network change resets the watchdog's stall backoff. A network that returns
   after recovery was held offline (`heldForNetwork`) resets the supervisor's backoff when it
   starts its attempt (the 10 s flap gap still bounds it). Fresh network signals keep the 30 s
   limit (ADR-143); `onAvailable` now also sends one when the network merely became usable (first
   network after an offline start). Offline, no attempt is made or counted (ADR-140, unchanged).
5. **Proof and escalation (4.4).** A handshake newer than the change (or than the restart it
   caused) is still the only proof. Without it, at most **2** restarts run per change
   (`NETWORK_RESTARTS`); if the second one also gets no handshake within the 15 s gap, the watchdog
   returns `NEW_LEASE`: native stops the tunnel with the existing code `handshake_not_established`
   (stage `native_watchdog_restart`, event `watchdog_new_lease network_unrecovered`) and the
   normal path takes over — a UI in the foreground sees the stop and reconnects on a new lease;
   otherwise the supervisor starts native recovery (category `transient`). A new change during
   verification starts a new budget. No new owner, state, error code or channel field is added.

## Assumptions
- 0.5 s is enough for a new default network to be stable; a later change replaces the scheduled
  check (one pending check at a time).
- 8 s covers ordinary handovers; a real loss is shown 8 s later than before.
- Reusing `handshake_not_established` (already allow-listed in Dart and Java) is accurate: no
  handshake was established on the new path. Dart maps a native stop with time left to
  `native_down` and reconnects; its own 20 s rule (ADR-145) is separate.

## Security and privacy
- `setUnderlyingNetworks` changes only what Android reports about the VPN network (metered,
  validated, transports, bandwidth). It does not route traffic: the tunnel socket stays protected
  and bound by the engine, and app traffic still goes only through the TUN interface. A wrong
  underlying network would, at worst, make apps see a wrong metered/unmetered or validated state
  (for example a large download on a metered path believed to be unmetered); it cannot expose
  traffic outside the tunnel.
- Lockdown / Always-on is unchanged (ADR-132): while offline or during a restart, Lockdown still
  blocks traffic outside the VPN. The 8 s grace changes only the displayed state, never routing or
  fail-closed limits; the native deadline still stops an expired window.
- The new lease path stops the tunnel; a stopped tunnel under Lockdown blocks traffic until
  recovery brings a new window up, as for any other native stop.
- Event-log entries are codes only (`underlying_set`, `underlying_skipped no_service`,
  `underlying_failed`, `network_loss_confirmed`, `watchdog_new_lease network_unrecovered`); no
  network id, address or endpoint is logged.

## Known limitations
- **Not verified on a device.** No APK was built and nothing was installed.
- **`setUnderlyingNetworks` needs the service instance.** Native holds it (`protector`) only for
  tunnels started by Always-on, boot restore, a sticky restart or native recovery. For a tunnel
  the UI started, `GoBackend` keeps its service private, and changing the reviewed, hash-pinned
  `go-only.patch` was out of scope; there the call is skipped (`underlying_skipped no_service`) and
  Android keeps its default behaviour. Plan Phase 6.1 (a protector for UI-started tunnels) would
  close this gap.
- The 15 s restart gap can delay acting on a second change shortly after a restart.
- Stopping for a new lease does not revoke the old server row at once; it ends at its deadline or
  is revoked by the next reconcile, as for `native_restart_failed`.
- The device scenarios in the plan (Wi-Fi ↔ LTE, airplane mode) were not run.

## Verification
- **Deviation from the required check:** `gradlew testDebugUnitTest` was **not** run (no Gradle
  wrapper in this worktree). Substitute, as for ADR-140..145: all app Java sources (client and
  shared trees, patched tunnel sources, generated registrant stubbed) compile with
  `javac --release 17` against Android SDK 36; JUnit 4.13.2 — **194 tests passed** (187 before):
  191 in the main run plus 3 `RestartInPlacePatchTest` run from `apps/client/android/app`.
  New/changed: `TunnelWatchdogTest` (0.5 s settle, change before the first tick, stall backoff
  reset, two restarts then `NEW_LEASE`, new budget per change), `PhysicalNetworkTest` (8 s loss
  confirmation cancelled by `onAvailable`), `ConnectionStateTest` (unconfirmed loss keeps a working
  tunnel connected), `ConnectionSupervisorTest` (returning network resets the backoff, offline
  makes no attempt, fresh signals keep the 30 s limit).
- Dart: no Dart change. `flutter test` from `apps/client` as a regression check — **310 passed**.
- `flutter analyze` on an ASCII-path copy (lib, test, pubspec, analysis options): no code issues;
  one expected warning that `assets/orbit/` was not copied.
- `ClientApplication` wiring (callbacks, timers, `setUnderlyingNetworks`) has no JVM test; it is
  compiled only.

## Owner phone steps (NOT VERIFIED — build from this branch)
Record only pass/fail, the status text and the event log from the diagnostic report.
1. Connected on Wi-Fi, turn Wi-Fi off (mobile data on): within about 10 s a new handshake; the
   event log shows `watchdog_restart` shortly after `network_available changed`; no long
   «Восстановление связи».
2. Turn mobile data off and Wi-Fi on quickly (under 8 s offline): the title should not switch to
   «Восстановление связи» for the gap alone.
3. Airplane mode for 2 min, then off: no recovery attempts while offline (event log), reconnect
   within about 10 s after.
4. After a boot or Always-on start, switch networks: the event log shows `underlying_set`. A tunnel
   started from the app shows `underlying_skipped no_service` (known limitation).

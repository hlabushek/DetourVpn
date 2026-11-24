# ADR-194 — Windows watchdog: engine crash, network change and sleep/resume

**Status:** implemented on branch `claude/connection-reliability` (committed locally, not pushed).
**Unit-tested against the fake engine only. Not run on a real Windows PC: no tunnel handshake,
no real network change, no real sleep/resume was observed.**
**Issue:** I-032 (Windows beta). Closes the code part of gap W-02 (sleep/resume) and the Windows
part of the watchdog parity work. Tray mode (the other half of W-02) is out of scope, see below.
**Builds on:** ADR-099/122/146 (Android `TunnelWatchdog`), ADR-148 (dead-peer probe), ADR-160
(monotonic handshake age), ADR-187 (rules apply on next connect), ADR-095 (Windows client).

## Context
On Windows the Dart controller owns recovery (`nativeOwnsBackgroundRecovery` is false). Before this
change the native `TunnelController` only noticed that the engine had exited or the window had
expired. Then Dart started a **new lease** (`native_down` → auto-reconnect). Nothing reacted to a
stalled handshake, a Wi-Fi/Ethernet switch or a resume from sleep. The UI only showed «Handshake не
получен … подключитесь заново». Android has handled these cases natively since ADR-099/146.

## Decision
1. **`tunnel_watchdog.h`** is a line-by-line C++ port of `TunnelWatchdog.java`, with the same
   constants (stale 180 s, fresh 60 s, settle 0.5 s, 15 s gap between network restarts, stale
   backoff 2 → 10 min, 2 restarts per network change, then a new lease). Pure, header-only, tested
   with the same scenarios as the Java tests.
2. **The controller's watch thread** (250 ms tick, under the controller mutex) now runs:
   - **Engine crash:** when an AWG engine exits on its own, the tunnel is restarted **in place on
     the same window** at most `crash_restarts` (2) times per `crash_window` (10 min). After that,
     or if the restart fails, it stops with `engine_exited` as before, and Dart's recovery takes
     over. `Snapshot()` uses the same path, so the UI never sees "down" without a reason.
   - **Physical network:** every 2 s the controller reads an in-memory identity of the usable
     physical IPv4 network. This is the set of up, non-loopback, non-virtual, non-Wintun adapters
     that have an IPv4 default gateway (`GetAdaptersAddresses`). A different identity, or a network
     that comes back after a loss, counts as a network change. No identity means no usable network:
     the watchdog waits.
   - **Sleep/resume:** `WM_POWERBROADCAST` (`PBT_APMRESUMEAUTOMATIC`/`PBT_APMRESUMESUSPEND`) in
     `flutter_window.cpp` calls `SystemResumed()`, which counts as a network change.
   - **Decision:** `RESTART` rebuilds the engine config from the inputs kept at connect and
     relaunches the engine. `NEW_LEASE` stops with `handshake_not_established`, the same code as
     Android's `watchdog_new_lease`. Dart's `_stopCode` maps it to `native_down`, so it
     auto-reconnects and keeps the user's wish to stay connected.
3. **Inputs kept for a restart:** the Dart profile text (it holds no private key; the vault supplies
   it at build time) and the `RoutingPolicy` built at connect. This keeps ADR-187: an in-place
   restart never applies split/DNS rules saved later. Both are wiped on every stop.
4. **Handshake rules only with evidence:** the stale-handshake and network rules run only after the
   engine has reported at least one `PEER_STATS` line on this connection. An engine build without
   peer stats would otherwise be restarted every few minutes. VLESS sessions (ADR-115) have no
   handshake signal, so they get neither the handshake rules nor crash restarts. Their engine death
   is reported as before.
5. **Clock:** the production `now_ms` hook is `GetTickCount64`, which keeps counting through sleep
   and hibernation. A window deadline therefore expires at its real time after a long sleep.
   Before, it used `steady_clock` (QueryPerformanceCounter), and the documentation does not
   promise that this counts sleep. If Dart's `Stopwatch` lags, native stops first with
   `lease_expired`, which is the safe direction.

## Dead-peer probe (A-03) on Windows: not implemented, and why
ADR-148's active HTTPS probe is inert on Android because no project-owned health URL exists (the
manifest value is the placeholder `none`). There may also be a socket-binding limit there. On
Windows the binding limit does not apply: only the API addresses bypass the tunnel. But the
missing health endpoint does apply, so a Windows probe would be equally inert. A probe aimed at the
API origin would go **around** the tunnel (the API bypass rule) and would prove nothing about the
data path. So no probe was added. On Windows the stall rule is the watchdog's 180 s
stale-handshake restart with backoff. The probe needs the same owner step as on Android: a
project-owned HTTPS health endpoint with documented retention. A Windows probe can follow as its
own change once that exists.

## Tray mode (W-02, second half): out of scope for beta.37
Today, closing the window stops the engine (`FlutterWindow::OnDestroy`). This is the documented
way for a tester to end the VPN. Tray mode would keep the tunnel running with no visible window.
Without a kill switch (ADR-196 is a design only), a hidden window plus a dead engine would leak
traffic silently. Tray mode also needs new Russian UI strings, a menu, single-instance handling
and manual testing on a real PC. It is not small, so it is deferred.

## Security and privacy
- No new logging. The network identity (adapter index plus gateway) is compared in memory only and
  is never logged, shown or sent.
- The private key is not kept beyond the existing `key_cache_`. The kept profile and policy contain
  no secret beyond what the running engine already has, and they are zeroed on stop.
- Restarts are bounded: crash restarts at 2 per 10 min; network restarts at 2 per change with a
  15 s gap; stale restarts back off from 2 to 10 min. A restart reuses the same window and so
  reserves no new quota. A `NEW_LEASE` goes through Dart's existing backoff.
- A restart briefly removes the TUN, exactly like a crash. Without a kill switch, traffic can go
  direct for that moment (as today on every engine death).

## Known limitations (NOT verified)
- No real Windows run: whether the engine really re-handshakes after an in-place restart, whether
  the Wintun adapter is excluded from the identity on every system (`IfType`/description match),
  whether Hyper-V/VPN-client virtual adapters cause false changes, and whether
  `WM_POWERBROADCAST` reaches the Flutter top-level window on Modern Standby devices.
- A restart runs under the controller mutex on the watch thread. A `snapshot` call from the UI
  thread can wait for one start probe (1.5 s), or up to the 10 s stop grace when an engine ignores
  Ctrl-C. That is the same bound as `Disconnect`.
- Constants are copied from Android; they were not measured on Windows.

## Verification
- `apps/client/windows/tests` (CMake + Ninja, MSVC, `/W4 /WX`): `config_factory_tests` 186
  checks, `adapter_tests` 165 checks, 0 failures. New cases: the pure watchdog scenarios; a single
  crash restarted on the same window (the window can still be advanced); a crash loop bounded to 2
  restarts and then `engine_exited`; VLESS never restarted; network change → restart; a loss
  waits; resume → restart; resume without a tunnel ignored; production hooks smoke check.

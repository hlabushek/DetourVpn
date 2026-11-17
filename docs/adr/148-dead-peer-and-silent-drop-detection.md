# ADR-148 — Dead-peer and silent-drop detection (connection-survivability Phase 5)

**Status:** implemented on branch `claude/connection-reliability` as Phase 5 of the owner-approved
connection-survivability plan; committed locally only. **Not verified on a device. The active
probe is off in this build (placeholder URL), and it may not be able to run at all on a phone
(see "Known limitations").**
**Builds on:** ADR-099/122/146 (watchdog, network-change restarts, new-lease step), ADR-127
(physical network), ADR-133 (data-path evidence), ADR-031/092 (node selection), ADR-132 (routing,
the app excludes itself from its VPN), ADR-144 (native state, single owner).
**Owner decision (2026-11-17):** automatic node failover on a confirmed dead peer — **yes**,
opt-in per ADR.

## Problem

- `DataPathWatch` (outbound traffic with no inbound answer) was sampled from the watch tick and
  also from every `snapshot()`, so the evidence still depended on how often Flutter polled.
- A stall only lowered the displayed state. Nothing checked whether the tunnel really carried
  traffic, so a silently dropping path was only restarted by the watchdog's 180 s handshake rule,
  and never escalated beyond restarts with backoff.
- A captive-portal network showed «Нет сети», with no hint that the user must sign in.

## Decision

### 5.1 Data path from the native watch tick only
- New pure class `DeadPeerWatch` owns the `DataPathWatch`. It is fed only by
  `DeadPeerWatch.tick(...)`, which `ClientApplication.watchTunnel()` calls on the native watch
  tick. `snapshot()` and `connectionState()` only read it (`flowing`, `stalled`).
- The Dart `DataPathHealth` tracker is **kept**, because it is not a duplicate on Android: the
  controller renders native's state (`_applyNative`) before the Dart rule is reached, so on
  Android it is never fed. It is still the only data-path rule for Windows and for older Android
  builds without a native state. Removing it would change Windows behaviour, which is outside
  this phase. A Dart test pins that Android ignores it.

### 5.2 Active probe through the tunnel and the escalation ladder
- **Suspected stall:** outbound bytes with no inbound answer for 60 s
  (`DataPathWatch.SUSPECT_MS`, same 8 KiB outbound threshold as ADR-133), or no handshake for
  180 s (`DeadPeerWatch.STALE_HANDSHAKE_MS`, also counted from window start when no handshake
  happened yet).
- **Probe:** one HTTPS `GET` to the configured health URL, sent on the VPN network
  (`Network.openConnection`), bounded to 5 s in total (connect, TLS, response; a timer on the
  worker enforces it). Only a 2xx status passes; a redirect or any error is a failure. One probe
  at a time; the second follows 5 s after a failure.
- **Ladder** (`DeadPeerWatch.probeResult`):
  1. a pass → the tunnel is healthy; the ladder resets; no probe for 120 s;
  2. two failures in a row → **restart in place** (`restartInPlace`, the same code path as the
     watchdog's restart);
  3. two more after that → **new lease**: the window is stopped with the new stop reason
     `dead_peer` (category `dead_peer`, transient), so recovery starts a new window as for
     ADR-146's new-lease step;
  4. two more → **failover**, only when the owner opted in; otherwise the ladder is exhausted;
  5. after failover, two more failures → exhausted.
  Each step gets 30 s before the next probe. An exhausted ladder probes at most every 10 min; a
  pass resets it. A network change or a user disconnect resets the ladder; a new lease keeps it.
- **Unavailable is not failed:** no probe URL, no VPN network, or a refused socket bind gives
  `UNAVAILABLE`, which never escalates and pauses probing for 10 min.
- **Who restarts a stall:** while the ladder owns stall recovery
  (`DeadPeerWatch.ownsStallRecovery`: probe configured, not exhausted, not paused after an
  unavailable probe), the watchdog's own stale-handshake restarts are off
  (`TunnelWatchdog.decide(..., staleRestarts=false)`); its network-change restarts and its
  new-lease step are unchanged. In every other case the watchdog behaves exactly as before. With
  the shipped placeholder the probe is off, so **this build behaves as before Phase 5** for stalls.
- **Failover (opt-in, default OFF):** manifest meta-data `org.example.vpnpoc.DEAD_PEER_FAILOVER`,
  shipped as `false`; only boolean `true` (or the string `"true"`) turns it on. When the ladder
  reaches the failover step, native stops the window with `dead_peer_failover` and sets a one-shot
  in-memory flag. The headless restore engine (native-owned recovery, `always_on_recovery.dart`)
  reads it once through the new recovery-channel method `failoverRead` and, for that one window,
  selects the first *other* AmneziaWG node that the catalog reports `available`
  (`failoverNode`). That is the existing selection rule (ADR-031/092 availability, the same
  `selectNode` check a manual choice uses); there is no new server path. The saved choice
  (`selected_node`) is not changed. With no other available node, the saved node is used. The
  flag is cleared on read, on any window start and on a user disconnect, and it is honoured only
  while the opt-in is on.

### 5.3 Captive portal
- `PhysicalNetwork.captivePortalBlocking()`: the default network has `CAPTIVE_PORTAL` and not
  `VALIDATED`. Such a network was already "not usable" (ADR-127), so no restart, probe or recovery
  attempt runs on it (unchanged).
- New: `ConnectionState` reports `RECOVERING{captive_portal}` instead of
  `waiting_for_network`, both with the tunnel up and down. `NetworkCallbacks.capabilities`
  publishes the state when a portal appears (`network_captive_portal` in the event log) and when it
  goes away. The UI shows «Сеть требует входа (страница авторизации Wi-Fi). Войдите в сеть — VPN
  подключится сам». After sign-in the network becomes usable and the existing fresh-signal path
  recovers at once.

## Health URL placeholder

- Location: `apps/client/android/app/src/main/AndroidManifest.xml`, meta-data
  `org.example.vpnpoc.DEAD_PEER_PROBE_URL`, value **`none`** — a clearly marked placeholder, not a
  URL. No health endpoint existed in the repository, and no real endpoint is hard-coded anywhere.
- `DeadPeerProbe.validUrl` accepts only `https://` with a lower-case DNS host name (no IP literal,
  no `localhost`), an optional port and a plain path of at most 128 characters; it refuses user
  info, query and fragment, so the URL itself cannot carry data. Anything else keeps the probe
  off.
- The endpoint must be **project-owned** and must answer 2xx to a bare `GET` without logging
  more than the server's minimum (see TECHNICAL_SPEC.md retention). Choosing and deploying it is an
  owner step (see below); it was not done here.

## Assumptions

- The tunnel carries the probe like any other traffic, so its answer is evidence of the data path.
- A failing probe can also mean the health endpoint is down. With failover off, the worst case is
  one restart and one new lease, then the exhausted ladder hands stalls back to the watchdog.
- The ladder state lives in memory only. A process death starts it over; that is acceptable since
  recovery then starts a new window anyway.
- Failover is applied only by the restore engine (native-owned recovery: background, Doze,
  Always-on). When the UI is in the foreground and owns reconnects (ADR-142), it reconnects to the
  selected node; the user sees the state and can pick another node.

## Security and privacy

- The probe sends no user data: a fixed URL without query or fragment, a fixed User-Agent
  `detour-probe/1` instead of the platform one (which names the device model), no cookies or
  credentials, no request body; only the status code is read. It runs only when a stall is
  suspected, at most every 5 s while failing and every 10 min when exhausted.
- The health endpoint learns that a probe arrived from a node's egress address at a time. It must
  be project-owned and must not be a third-party service.
- Nothing about the probe is logged except codes (`probe_start`, `probe_pass|fail|unavailable`
  with `pass|status|io|timeout|no_vpn_network|bind_refused|not_started`, `probe_restart`,
  `probe_new_lease`, `probe_failover`, `failover_read`, `probe_config on|off[_failover]`). No URL,
  host, address or payload.
- Failover changes which node a window uses without a click. It is off by default and must be
  turned on deliberately in the manifest. It never changes the saved node, never selects an
  unavailable or panel node, and each new window still goes through the normal lease, quota and
  key flow.
- No new permission. `ACCESS_NETWORK_STATE` and `INTERNET` were already declared.

## Known limitations — NOT verified

- **The probe may never run on a phone.** This app excludes its own package from its VPN
  (ADR-132, `ClientProfile.packagesForMode`). Android normally refuses to bind a socket to a VPN
  network for a UID outside that VPN. The code then reports `UNAVAILABLE` (`bind_refused`), which
  never escalates, so Phase 5.2 would be inert, not harmful. Making it effective needs a separate
  routing decision (for example, letting the app's own UID use the tunnel for this request only),
  which this phase does not make. **This is the first thing a device run must check.**
- `getAllNetworks()` visibility of the VPN network to an excluded UID is also unverified.
- No device run, no silent-drop scenario (the staging nft drop rule in the plan's test matrix needs
  explicit staging authorisation), no captive-portal network test.
- No Gradle build, no manifest merge, no lint. The new `<meta-data>` entries were not checked by
  aapt.
- Real network behaviour of the probe (DNS through the VPN, TLS, the 5 s bound) is not tested;
  only the pure rules are.
- Play policy: no new permission or foreground-service type; the earlier ADR-147 Play checks still
  apply. Not checked here.
- The 60 s / 180 s / 5 s / 30 s / 120 s / 10 min constants are design choices, not measurements.

## Verification

Run for this change (2026-11-17, local, no device):
- Java substitute for `gradlew testDebugUnitTest` (a **deviation**, not a Gradle pass): `javac
  --release 17` against Android SDK 36 and the cached jars, JUnit 4.13.2 — main run 235 passed,
  `RestartInPlacePatchTest` 3 passed (238 in total; 209 before). New: `DeadPeerWatchTest` (data
  path fed only by the tick; probe triggers; pass; one failure then pass; restart → new lease →
  stop without opt-in; → failover only with opt-in; settle time; exhausted ladder; unavailable;
  lost and stale results; resets), `DeadPeerProbeTest` (placeholder off, failover default off, URL
  rules, status rule), plus cases in `ConnectionStateTest`, `NetworkCallbacksTest`,
  `TunnelWatchdogTest` and `DataPathWatchTest`.
- `flutter test` in `apps/client`: 315 passed (310 before). New: captive-portal hint, Android
  ignoring the Dart data-path rule, `failoverNode`, restore with and without a failover request.
- `flutter analyze` on an ASCII-path copy: only the expected `assets/orbit/` warning.

## Owner steps (NOT VERIFIED)

1. Decide whether the probe should run at all, given the routing limitation above; if yes, decide
   how the app's own probe request may use the tunnel (a separate routing ADR).
2. Provide a project-owned HTTPS health endpoint that answers 2xx to a bare `GET`, document its
   log retention, and put its URL in `DEAD_PEER_PROBE_URL`.
3. Only after a device run shows real probe results, consider `DEAD_PEER_FAILOVER=true` for a test
   build, with at least two accepted nodes.
4. Device checks: a silent drop on an authorised staging node (probe fails → restart → new lease,
   in the event log), a captive-portal Wi-Fi (state `captive_portal`, no restarts until sign-in).

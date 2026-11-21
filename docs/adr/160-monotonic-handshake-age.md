# ADR-160 — Handshake ages are monotonic; a wall-clock step is not a stale handshake

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Unit-test
verified only (Java substitute); not verified on a device.**
**Builds on:** ADR-099/122/146 (watchdog), ADR-148 (dead-peer watch), ADR-151 (handshake after the
last change).

## Finding
The engine's handshake time is wall-clock based. `TunnelProviderV1.lastHandshakeSeconds()` →
`AwgProviderV1` → `GoBackend.getLastHandshake`, which returns the UAPI field
`last_handshake_time_sec`. wireguard-go fills that field from `time.Now()` at the handshake, i.e.
Unix wall-clock seconds. `ClientApplication` then computed the age on every read as
`System.currentTimeMillis()/1000 - handshake` (in `connectionState()`, `watchTunnel()` and
`snapshot()`).

Consequences of a wall-clock step (manual change, carrier NITZ, NTP correction):
- forward step: the last handshake suddenly looks hours old → `TunnelWatchdog.decide` sees a stale
  handshake and restarts the tunnel in place (a visible glitch), the state turns `RECOVERING`, and
  the evidence/diagnostics report an old handshake;
- backward step: `handshake > now` was treated as "no handshake" → `no_handshake` state and, after
  `STALE_MS` of uptime, a restart as well.

All other ages in the watch stack (network change, window start, restarts, deadline) already use
`SystemClock.elapsedRealtime()`.

## Decision
- New pure `HandshakeClock`: at the first observation of each new handshake value, its age is taken
  from the wall clock once and anchored to `elapsedRealtime()`. Every later age of the same value is
  `elapsedRealtime() - anchor`. A new value (rekey, restart) is anchored again. A value of 0 or less
  resets the anchor.
- A value that is in the future at its first observation (the clock went back between the handshake
  and the first read, at most one watch tick) reports -1 as before, until the next handshake.
- `connectionState()`, `watchTunnel()` and `snapshot()` use the one shared `HandshakeClock`. The
  ADR-151 rule is unchanged: the age is compared with the monotonic age of the last network change,
  so a handshake older than the change still proves nothing.
- `HandshakeMemory` (diagnostics "a handshake was seen") is unchanged; it only needs the value to be
  positive.

## Alternatives
- Patch the engine to export a monotonic handshake time: rejected for now — it changes
  `go-only.patch` and its pinned hash and needs a native rebuild; the anchor gives the same result
  for every handshake observed within one tick.
- Use only "the handshake value changed" as freshness: loses the age needed by the watchdog,
  the dead-peer watch and the diagnostics buckets.

## Assumptions
- The handshake value is observed within one watch tick (or a snapshot/state recheck) of the
  handshake, so the one wall-clock subtraction at anchoring is exposed to a clock step only during
  that short interval.

## Security and privacy
No change to the kill switch, Lockdown, the deadline or fail-closed behaviour; the watchdog still
restarts a genuinely stale tunnel. No new data is logged.

## Test plan and results
- `HandshakeClockTest` (8 tests): no handshake, unchanged behaviour without a clock step, forward
  and backward steps, re-anchoring on a new handshake, a future value at first sight, reset, and the
  acceptance test `aClockJumpDoesNotTriggerAStaleHandshakeRestart` (a two-hour forward step leaves
  a 150 s age and `TunnelWatchdog` decides NONE; the former wall rule decides RESTART).
- `ConnectionWiringTest.handshakeAgesAreMonotonic`: static guard that the three call sites use the
  clock and no per-read wall subtraction remains.
- Java substitute (javac `--release 17` + JUnit 4.13.2 against the c6 SDK tree, not Gradle): main run
  **262 passed**; from `apps/client/android/app` **11 passed**.

## Not verified
No device run and no real clock-step scenario; no Gradle build. The Windows client computes its own
handshake state and is not changed here.

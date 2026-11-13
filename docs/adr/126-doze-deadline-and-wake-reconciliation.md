# ADR-126 — Doze: event-driven deadline and reconciliation on wake

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2).

## Context (verified in code at `144080f`)
- The native deadline was enforced by a `scheduleWithFixedDelay` loop every 250 ms for the whole
  life of the process, tunnel or not (`ClientApplication.java:112-118`). It never wakes the CPU,
  but it runs four times a second whenever the CPU is awake (which is often with a VPN carrying
  traffic) and is the kind of constant polling the task forbids.
- `ScheduledExecutorService` delays use a clock that stops in deep sleep; `elapsedRealtime` does
  not. A delayed timer callback must therefore never be taken as "the window is still valid".

## Decision
1. `setDeadline(end)` arms one one-shot check at the deadline (elapsed-realtime based delay);
   every deadline change re-arms it; `stop()` cancels it.
2. `enforceDeadline()` (fail closed: stop, reason `lease_expired`, supervisor notified) also runs
   at the start of every event that executes after a possible sleep: the 15 s watchdog tick,
   `snapshot`, the native renewer's view, network callbacks via the watchdog, and the recovery
   alarm. It compares `elapsedRealtime` with the deadline, so sleep time counts.
3. Worst case after a wake-up: the local stop happens at the first of those events (≤ 15 s of
   awake time). During that gap no traffic is authorized anyway: the node removes the peer at
   `not_after` (ADR-120 margin), so the tunnel carries nothing (fail closed, no leak: the TUN
   still holds the routes).
4. Unchanged and kept: screen-off, idle, backgrounding and missing traffic never trigger a
   restart or a stop. Only the watchdog's handshake evidence (ADR-099/122/128), the deadline and
   explicit stops do.
5. On every wake the supervisor reconciles: deadline, physical network (callback state), tunnel
   up/down, intent, pending recovery (alarm) — one `evaluateRecovery()` call.

## Limits
- Doze delays allow-while-idle alarms to about one per 9 minutes; renewal timing in deep Doze is
  ADR-118's limit and unchanged.
- No exact alarms (would need `SCHEDULE_EXACT_ALARM`, not justified for a VPN).

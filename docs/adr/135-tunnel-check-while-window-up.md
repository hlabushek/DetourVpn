# ADR-135 — The tunnel check runs only while a window is up

**Status:** accepted 2026-11-13 (connection reliability run 3, item F; battery).

## Context
`ClientApplication` ran `watchTunnel` every 15 s for the whole life of the process. When no tunnel is
up, `watchTunnel` only resets the watchdog and returns. The periodic wake-up bought nothing while
disconnected, and the process can stay alive for a long time in the background.

## Decision
- No periodic task is scheduled at process start.
- `setDeadline` (a window starts or is adopted) arms one check after 15 s (`armWatch`).
- Each check that finds a tunnel up re-arms the next one. A check that finds no tunnel stops the chain.
  `armWatch` cancels the previous pending check, so there is never more than one periodic chain.
- The pending network check (ADR-122) and the deadline one-shot (ADR-126) are unchanged.

## Why only this change in the schedule
Every other periodic item on the connected path has a stated reason (see `RELIABILITY3_BATTERY.md`).
The WireGuard keepalive (25 s) and rekey (120 s) are not changed: the stall threshold (ADR-133), the
watchdog stale rule (180 s) and the freshness rule (60 s) depend on them.

## Security considerations
- A stopped chain only stops health checks. Deadline enforcement is a separate one-shot task and is
  unchanged (fail closed, ADR-126).

## Limits / not verified
- Android scheduling cannot be unit-tested. The change is code-reviewed; the battery effect is not
  measured (a device or battery-historian run is needed).
- If a window were ever adopted without `setDeadline`, the check would start late. Adoption goes through
  `setDeadline` in the current code (`advanceNative`, `connectWindow`); this is a reading, not a test.

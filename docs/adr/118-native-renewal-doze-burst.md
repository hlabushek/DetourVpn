# ADR-118 — Native renewal retries inside one wake-up before waiting for the next alarm

**Status:** proposed 2026-11-11 (connection reliability, `RELIABILITY_ROOT_CAUSE.md` §3).
**Amends:** ADR-097.

## Context
`NativeRenewer` makes one attempt per wake-up. On a transient failure it schedules a short
in-process retry and an `AlarmManager.setAndAllowWhileIdle` alarm, then releases the wake lock.
In Doze the CPU sleeps right after that, so the in-process timer does not run. The alarm is the
only wake source. Android documents that allow-while-idle alarms fire at most about once per
9 minutes per app in Doze. A 900 s window starts renewing with 10 minutes left. So in deep Doze
there is room for about **one** attempt per window. A single transient failure at wake-up
(radio still coming up, DNS, a slow node acknowledgement) can therefore end the window.

The same applies when the renewal is accepted but the node acknowledgement takes longer than the
30 s pending poll: the next look is one alarm (about 9 minutes) later.

## Options considered
- `setExactAndAllowWhileIdle`: needs `SCHEDULE_EXACT_ALARM`, which is denied by default for new
  installs on Android 14 and is meant for alarm and calendar apps. The Doze rate limit still
  applies. Rejected.
- Longer windows: 900 s is a node contract (`MAX_WINDOW_SECONDS`). Out of scope.
- Start renewing earlier: in deep Doze the alarm cadence, not the threshold, limits attempts.
  It would only add API load while awake. Rejected.
- **Retry inside the same wake-up** while the existing 60 s partial wake lock is held. Chosen.

## Decision
- A new pure policy `NativeRenewal.burstDelayMs(attempt, elapsedMs, remainingMs)` returns the delay
  before the next in-wake attempt (2 s, 4 s, 8 s), or `-1` when the burst budget is spent. The
  budget is at most 4 attempts, none starting 30 s or more after the wake-up began (pending polls stop at 40 s), and never past the 6 s
  safety margin of the window.
- `NativeRenewer.run` repeats the attempt within that budget. A "pending" result (renewal accepted,
  node not yet acknowledged) counts as a retry; the repeat re-sends the same idempotency key, which
  the server answers with the same hold.
- After the burst, the existing backoff and alarm path is unchanged.

## Security considerations
- Same endpoints, token and idempotency key. At most 4 extra requests per wake-up.
- The wake lock stays capped at 60 s.

## Limits / not verified
- Not verified on a physical device. Doze timing differs by OEM; some OEM battery managers ignore
  the battery-optimization exemption.
- If the network is unavailable for the whole burst, the window can still end. Then ADR-099
  automatic reconnect applies once the app runs.

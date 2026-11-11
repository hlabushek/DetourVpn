# ADR-122 — Network-switch recovery: no dropped triggers, and an honest "connected"

**Status:** proposed 2026-11-11 (connection reliability; owner phone test 4).
**Amends:** ADR-099.

## Context
Owner report from phone test 4 (build 0.1.0-beta.28, not reproduced in code by a device): after
switching from Wi-Fi to mobile data during a session with many quick network changes, the app
showed the VPN as connected but no traffic passed. Toggling airplane mode restored it.

Verified in code (`TunnelWatchdog.shouldRestart`, before this ADR):
1. A network change is acted on once, 3 s after it settles. If the previous restart was less than
   15 s earlier, the trigger is **cleared and dropped**, not postponed. After quick changes, the
   last real change can be ignored.
2. Network-change restarts and stall restarts share one backoff counter. Each network restart makes
   the stall rule wait longer (2, 4, 8, 10 min). So once a trigger is dropped, recovery can take
   minutes.
3. There is no second try if the first restart after a change also gets no handshake.
4. The status treats any handshake younger than 60 s, or inbound bytes in the last 60 s, as proof
   that the tunnel works, even if both happened on the old network before the switch. So the app
   can say "connected" for up to a minute with a dead path.

Airplane mode produced a new loss and a new "available" event, so a new trigger fired. That
matches the report. That this is exactly what happened on the phone is a hypothesis.

## Decision
- A network-change trigger stays **pending** until it can act: it waits for the 15 s gap and for a
  validated network, instead of being dropped. It is cleared only when it restarts the tunnel or
  when a handshake happened after the change.
- After a network-change restart, the watchdog checks again (same 3 s settle, 15 s gap). If no
  handshake followed the restart, it restarts again, at most 2 more times per change.
- Network-change restarts no longer raise the stall backoff. Only stall restarts do.
- While a trigger is pending, the app schedules the next check for when it becomes eligible,
  instead of waiting for the 15 s tick.
- The native snapshot reports `networkChangeAgeMs` (time since the last default-network change;
  -1 if none). Dart counts a handshake as proof only if it is newer than that change. Within 60 s of
  a change, recent inbound bytes alone do not count. Until proof arrives, the status says
  «Сеть сменилась, восстанавливается связь».

## Security considerations
- A restart is the same in-place restart as ADR-099: same connection, keys and window, no API call,
  fail closed on error. At most 3 restarts per network change, at least 15 s apart.
- `networkChangeAgeMs` is a duration only; no network name, address or type.

## Limits / not verified
- Not verified on a physical device. If the new network blocks the node's UDP port or AmneziaWG
  traffic, restarts cannot help; the status then honestly stays "restoring".
- The default-network callback runs only while the app process is alive (it is while the VPN runs).

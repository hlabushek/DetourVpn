# ADR-154 — A connect tap waits for a running poll instead of being dropped

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-099/119 (auto-reconnect waits for a poll), ADR-142/144 (foreground re-sync).

## Problem (tester report: connecting to a server needs retries)
`WindowConnectionController.connect()` began with `if (busy || _polling || _dead) return;`
(`window_connection.dart`, `connect`). A tap on «Подключить» / «Повторить» while a poll ran did
nothing and showed nothing.

Polls are long exactly when the user is most likely to tap:
- on return to the app the controller is unsynced (`appForegroundChanged`: `if (_id == null)
  _synced = false`), and `main.dart` starts a `refresh()` on `resumed`; that poll reconciles
  (native snapshot, `native.disconnect()`, `GET /window-tunnels`, revokes), each API call bounded
  only by the 8 s API timeout;
- at a cold start the first poll registers the device and reconciles;
- while a revoke is pending, every poll retries the DELETE.

The automatic paths already handled this (`_scheduleAutoReconnect` and `_scheduleResume` wait while
`_working || _polling`), but a user tap did not, so the user had to tap again.

## Decision
- When a poll is in flight, `connect()` marks itself busy (`_working`, so the screen shows the
  attempt with «Отменить подключение» and no new poll starts), awaits that poll, and then connects.
- After the wait it does **not** connect when the poll left a connection or a pending revoke
  (`hasLease || remotePending`), so a tunnel the poll adopted from native, or a server row still
  being closed, is never replaced by a second connection; or when the user cancelled meanwhile
  (`busy`, `_dead`).

## Alternatives
- Cancel the poll: rejected, a reconcile half done (native stopped, rows partly revoked) is exactly
  the state ADR-145 avoids.
- Queue the tap for later without feedback: rejected, it hides the attempt from the user.

## Security and privacy
No new data, call or permission. The guards that prevent two connections per device (reconcile,
`_closeOldLease`, the server refusing a second live connection) are unchanged; the new post-wait
check only adds one more refusal.

## Test plan and results
- `flutter test` (apps/client): **320 passed** (318 before). New:
  `ADR-154: a connect tap during a poll waits for it instead of being dropped` (busy at once, one
  create after the poll), and the negative `a poll that leaves a revoke pending is not replaced by
  the tap` (no create).
- `flutter analyze` on an ASCII copy: only the expected `assets/orbit/` warning; `dart format`: clean.
- No Java change.

## Not verified
No device run. Owner phone step: put the app in the background for a minute while disconnected,
reopen it and tap «Подключить» at once; it must start connecting on the first tap.

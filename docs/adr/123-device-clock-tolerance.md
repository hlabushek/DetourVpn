# ADR-123 — Tolerate a device clock that differs from the server

**Status:** proposed 2026-11-12 (tester report: Windows, every connect fails with
`invalid_response` at stage `lease_validation`, any server; the owner's PC works).

## Context
The client refuses a window whose `issued_at` is more than 1 s ahead of the device's wall clock
(`_bound` in `window_connection.dart`; the same rule in `NativeRenewal.adopt` on Android). It
also takes the earlier of two deadlines: one from the monotonic clock (`intent + duration − 6 s`)
and one from the wall clock (`now + (not_after − wall now) − 6 s`). So:
- a device clock a few seconds **slow** fails every connect with `invalid_response`;
- a device clock **fast** by more than the window length fails with `lease_expired`, and a
  smaller error shortens every window.

Windows PCs often drift by seconds when time sync is off. Not verified on the tester's PC: the
report gives only the code and the stage, so the clock is the most likely cause, not a proven one.

## Decision
- A window this client asked for (initial connect, Dart renewal, Android native renewal) was
  issued after the request was sent. Its deadline is the monotonic one only:
  `intent + duration − 6 s`. That is never later than the server's `not_after − 6 s`, whatever
  the device clock says, so the node gate still closes first.
- The wall clock is used only to detect a clock that is far off: more than **5 minutes** ahead
  of `issued_at`, or more than 5 minutes behind it (allowing for the time since the request).
  The client then stops with the new code `device_clock_skew` and a Russian text that tells the
  user to turn on automatic time. It is not retried automatically.
- A window the client did not ask for (adopting a renewal finished by the Android restore engine,
  ADR-121) keeps the earlier-of-two deadline, with the same 5-minute tolerance for `issued_at` in
  the future. If it fails, the app reconnects as before.

## Limits
- Unit tests only; not verified on the tester's PC or on a phone.
- 5 minutes is a choice, not a measured value: large enough for any drift a PC has without
  sync, small enough to catch a wrong date or time zone setting.

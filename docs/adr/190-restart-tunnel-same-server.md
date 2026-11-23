# ADR-190 — Restart the tunnel on the same server (`restartTunnel`)

**Status:** accepted. Code committed in 086f5e7 (`apps/client/lib/window_connection.dart`,
`apps/client/lib/settings_screen.dart`); tests in e56f692. **Not built into an APK, not run on a
phone or on Windows.**

## Context
ADR-187 lets the user save split rules while connected; the running tunnel keeps the rules it was
built with until the next connect. The user needs a way to apply the saved rules now without a
manual disconnect, which would also end the native "stay connected" wish (ADR-124).

## Decision
1. **Purpose.** `WindowConnectionController.restartTunnel()` stops the running tunnel and connects
   again to the same `selectedNode`, so every setting read at connect takes effect: today the saved
   split rules (ADR-187); in future any other connect-time setting that is allowed to change while
   connected. It is not a recovery mechanism and is not called automatically.
2. **It keeps the stay-connected wish.** It calls `disconnect(user: false)`, which stops the engine
   natively and settles the old connection on the server, but does not call `endUserIntent`
   (ADR-124). Then it waits for any running poll and calls `connect()`.
3. **No-op guards.** It returns at once when the controller is disposed, when there is no lease
   (nothing to restart), or when a switch or another restart is running (`_switching`, shared with
   `switchNode`, ADR-189).
4. **Entry point.** After leaving the split editor, if a change was saved while connected, the
   settings screen asks «Применить новые правила?» with «Позже» (keep the tunnel as it is) and
   «Перезапустить» (call `restartTunnel`).

## Security notes
- The restart is a full teardown and a fresh connect: a new key challenge, a new server connection
  and a new quota reservation, with the old connection revoked first. No in-place rebuild from
  preferences exists (ADR-187 "Native review").
- **Open question: Lockdown fail-closed during the teardown.** Between the native stop and the new
  start there is no tunnel. With Android Always-on + "Block connections without VPN" the system is
  expected to block traffic in that gap, but this has **not been verified** for this path: it is not
  known whether stopping the VpnService and starting it again keeps the Lockdown block continuous,
  or whether apps outside the split rules see a short window on the physical network. Without
  Lockdown, traffic in the gap is not tunnelled. On Windows there is no OS-level kill switch in this
  path; the gap behaviour is also unverified. **Do not describe the restart as leak-free** until a
  device test shows it.
- If the new connect fails, the VPN stays off on the same server with the usual error; there is no
  automatic choice of another server (ADR-092).

## Verification status
- Dart (TEST ONLY fakes, `apps/client/test/window_connection_test.dart`, group
  "ADR-190 restartTunnel on the same server"): reconnects to the same node, revokes the old
  connection first, creates exactly one new connection, does not end the native intent; no-op
  without a lease and while a switch runs. Group "ADR-187 apply dialog after a split change while
  connected": «Позже» leaves the tunnel untouched, «Перезапустить» restarts it, no dialog after a
  change made while off.
- Not verified: on a phone or on Windows; that the restarted tunnel really applies the new rules;
  the Lockdown question above.

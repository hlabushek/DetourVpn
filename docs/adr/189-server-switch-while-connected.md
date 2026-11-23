# ADR-189 — The server picker stays usable while connected (refines ADR-119)

**Status:** accepted. Code committed in 7053c08 (`apps/client/lib/region_picker.dart`); tests in
e56f692. **Not built into an APK, not run on a phone or on Windows.**

## Context
ADR-119 added `WindowConnectionController.switchNode`, but the picker screen still locked the list
while a lease was held, so a user had to disconnect, pick a server and connect again. The owner asked
for a direct switch from the picker while the VPN is on.

## Decision
1. **Only an operation in progress locks the list.** `RegionPickerScreen` disables the server cards
   when `controller.busy` (connecting or disconnecting) and shows «Дождитесь завершения подключения
   или отключения.» With a live lease and no operation running, every server whose reason is
   `available` is tappable, and the screen shows «Выбор другого сервера переподключит VPN к нему.»
2. **A tap on another server while connected switches.** `_select` calls `switchNode(id)` when the
   choice differs from `selectedNode` and the controller has a lease or is busy; otherwise it only
   calls `selectNode`. The picker closes at once; the switch runs in the background.
3. **Switching = disconnect(user: false) + connect() on the new node** (`switchNode` in
   `apps/client/lib/window_connection.dart`). The old connection is stopped natively and revoked on
   the server (a pending revoke is waited for, bounded by `revokeWaitChecks`); then `selectedNode`
   is set and `connect()` creates a new connection on the chosen node. A second switch while one is
   running is ignored. Choosing the already-connected node does nothing.
4. **The choice is remembered** through `NodePreferenceStore` when native offers it, as for a choice
   made while disconnected.

## What the user sees
The picker closes; the home screen shows the disconnect and then the connect phases, ending in
«Подключено» on the new server, or the usual error text if the new connection fails. A failure
leaves the VPN off on the new selection; it never falls back to the previous server.

## Security notes
- **No automatic node selection** (ADR-092). A switch happens only on the user's tap; a failed
  switch does not pick another node.
- **The old tunnel is torn down before the new one starts.** `connect()` refuses to create a new
  connection while the server still holds the old one (`revoke_unconfirmed`), and native refuses a
  start while a tunnel is up (ADR-187 "Native review"). There is never more than one tunnel or one
  server-side connection for the device at a time.
- `disconnect(user: false)` keeps the native "stay connected" intent (ADR-124), so a switch is not
  treated as a user disconnect.
- **Gap between tunnels.** Between the stop and the new start there is no tunnel. Whether traffic
  is blocked in that gap depends on Android Always-on + "Block connections without VPN" (Lockdown)
  and, on Windows, on the engine's own behaviour. This is **not verified** for the switch path; see
  ADR-190 for the same open question on restart.
- A switch creates a new connection and therefore a new quota reservation; the old reservation is
  settled by the revoke as for any disconnect.

## Verification status
- Dart (TEST ONLY fakes, `apps/client/test/window_connection_test.dart`, group
  "ADR-189 server picker while connected"): with a live lease another available server is tappable,
  the picker closes and the controller ends on the new node, with the old node revoked first and
  exactly one new connection; a busy controller shows the wait notice and a tap changes nothing.
  The ADR-119 group covers `switchNode` itself (pending connect, second switch ignored).
- Not verified on a device or on Windows: the real stop/start timing, the gap behaviour under
  Lockdown, and what the home screen shows during a real switch.

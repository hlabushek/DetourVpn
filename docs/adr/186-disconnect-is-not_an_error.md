# ADR-186 — A user disconnect is not shown as a revoke error when the server accepts it

**Status:** accepted. Code in the working tree, uncommitted. Not on a phone yet.

## Context
After a user tapped «Отключить VPN», the app showed the warning `revoke_unconfirmed` (diagnostic
code, stage `tunnel_monitor`) every time, even though the disconnect was intended.

Cause: the window connection DELETE (`services/api/src/vpn_api/tunnels/window_routes.py`) answers
202 with state `revoke_pending`. The node closes the tunnel on a later poll. The client treated any
connection that was not yet `closed` as «revoke not confirmed» and set the error, although the
close had been accepted.

## Decision
In `apps/client/lib/window_connection.dart` (the disconnect path) the warning is set only when the
DELETE call itself failed (`revokeErrorCode != null`). An accepted close that is still pending keeps
polling until the node confirms it, without a warning. The existing tests for a failed DELETE
(network failure shows the warning and keeps polling; 404 clears the id) are unchanged.

## Not covered
- The connect-time paths that throw `revoke_unconfirmed` (old lease cleanup) are unchanged.
- The test suite's fake server answers DELETE with `closed`, so the new behaviour for the real
  `revoke_pending` answer is not covered by a test yet. Add one when touching this again.

## Verification
- `flutter test` on an ASCII copy of `apps/client`: 362 passed.
- `flutter analyze` on the same copy: no issues.
- Not run: Android build, a phone run.

## Related
- ADR-145 (revoke pending), ADR-154 (connect waits for the poll).

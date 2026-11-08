# ADR-091 — self-service account deletion (tombstone)

Status: proposed 2026-11-08, implemented locally on branch `claude/beta-definition`.
Not deployed. Required by the ADR-090 go/no-go list.

## Context

The quota ledger, receipts, window holds, reward and referral rows reference `users` with
`ON DELETE RESTRICT` and are immutable accounting records (integer bytes and random ids). Hard
deleting a user would either fail or destroy reconciliation. The only personal link is the
Telegram subject in `auth_identities` (plus short-lived `login_attempts` and a keyed
`beta_invites` digest).

## Decision

`POST /api/v1/me/delete` with body `{"confirm": "delete-my-account"}`, native sessions only
(the Mini App session class is not granted `account:delete`). In one transaction, under the
user lock taken by `authenticate`:

1. revoke every device and enqueue removal of its tunnels/peer windows
   (`revoke_device_tunnels`, same path as device revocation);
2. revoke all session families;
3. revoke the subject's beta invite, so a returning person needs a new admin invite and cannot
   farm a fresh initial grant;
4. delete the subject's `login_attempts` and the `auth_identities` row;
5. set `users.status='deleted'`, `users.deleted_at=now` (migration `0018_account_deletion`,
   check constraint ties the two).

Accounting rows are kept, linked only to the tombstone's random id. Replaying the request
returns 401 because the session is gone. Wrong or missing confirmation returns 422 and changes
nothing.

## Consequences and limits

- Telegram identifier is removed from the application database. Previously taken encrypted
  backups still contain it until they expire under the backup retention (documented in the
  runbook; retention itself is an open owner decision).
- Device public keys stay on revoked `devices` rows (unique constraint, not personal data by
  themselves, but linkable to the tombstone). Not purged.
- If invite enforcement is off (`VPN_BETA_INVITES_ONLY=false`), a returning Telegram user
  creates a new account with a new initial grant. The beta runs with invites enforced.
- Unspent quota is forfeited; no refund path exists (no payments in beta).
- Rollback: the migration refuses to downgrade while any deleted account exists.
- Not covered: node-side peer removal still depends on the node agent acknowledging the queued
  removal (same as device revoke); a deleted-account drill on a real node was not run.
- Admin-initiated deletion on a tester's request is not implemented; the owner can revoke the
  invite and devices through the existing admin operations.

## Integration blocker (found 2026-11-08)

The owner's working tree contains an uncommitted migration `0018_peer_window_resolution`
(revision 0018, parent `0017_login_delivery_receipt`) that is already applied to the beta
database. This ADR's `0018_account_deletion` has the same number and parent, so both cannot be
merged as they are: Alembic would see two heads, and the restore drill of the real beta archive
fails with `schema_mismatch` on this branch. Before any merge, renumber to `0019_account_deletion`
with `down_revision = "0018_peer_window_resolution"` once that migration is committed, and resolve
the overlapping edits in `db/models.py`, `tests/test_migrations.py` and the Flutter files
`window_connection.dart` and `connection_screen.dart`, which are also modified in the working tree.

# ADR-110: Store the Telegram username for admin search (reverses ADR-109)

Status: accepted by the owner's request (overnight run 3, 2026-11-11). Implemented locally, not
deployed, not committed. Supersedes ADR-109 (option B there).

## Context
The owner wants to find testers in the admin panel by their Telegram username. ADR-109 kept the
username out of the database to minimise personal data. The owner has now chosen to store it.

## Decision
1. **Where:** nullable column `auth_identities.telegram_username` (migration
   `0026_telegram_username`). Check constraint: NULL or `^[A-Za-z0-9_]{4,32}$` (Telegram rules
   are 5–32; 4 allows collectible usernames).
2. **When it changes:** only the Mini App login sees the username (`initData.user.username`).
   On every successful Mini App login it is overwritten with the value Telegram sends. If
   Telegram sends no username (the user removed it) or a value that fails the format, the column
   is set to NULL. A bad or missing username never fails the login. The bot code login has no
   username and leaves the column unchanged.
3. **Purpose:** admin support only — find and recognise a tester.
4. **Who can see it:** authenticated admin sessions reading `GET /api/admin/v1/users`. It is never
   returned to client apps, the Mini App or the bot. It is stale between logins.
5. **Search and sort:** admin search `q` accepts `@name` or `name` and matches case-insensitively
   and exactly on `lower(telegram_username)`. No index: beta scale is small, and a functional
   index risks `alembic check` drift; add one if the user count grows. The UI sorts the loaded
   page by username, like the Telegram ID sort (ADR-105).
6. **Logging:** never logged; not added to error details, audit reasons or diagnostics.
7. **Retention:** kept while the account exists. Account deletion (ADR-091) deletes the
   `auth_identities` row, so the username is removed with it. Encrypted backups keep it until
   they expire (7 days, ADR-090).

## Consequences
- More personal data in the control plane; the privacy policy needs one line (owner action).
- Rollback: the downgrade drops the column and its index; usernames are lost (no other copy).

## Verification
- Migration and model tests, Mini App login tests (set, overwrite, clear, invalid → NULL),
  admin list/search tests, admin UI contract test. Results in the overnight report.

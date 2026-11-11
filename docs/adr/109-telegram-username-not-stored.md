# ADR-109: Telegram username is not stored; admin search stays on IDs

Status: **superseded by ADR-110** (run 3, 2026-11-11: the owner chose option B). Originally
accepted for the beta in overnight run 2, 2026-11-11.

## Context
The owner asked for the admin user list to show and search by Telegram username. Run 1
(ADR-105) added the numeric Telegram ID and found that the username is not stored anywhere.

## Findings (verified in code)
- The only Telegram data the API keeps is the numeric ID, in `auth_identities.provider_subject`
  with provider `telegram_bot` (`services/api/src/vpn_api/db/models.py`).
- `services/api/src/vpn_api/auth/mini_app.py` validates Telegram `initData` and states that
  raw `initData` and profile fields are never persisted. The username is one of those profile
  fields. This is a deliberate privacy choice, not an omission.
- `AdminIdentity.username` in `models.py` is the admin login name, unrelated to Telegram.
- The bot (`apps/telegram-bot`) reads the bot's own username for command routing only.

## Decision
1. Do not add storage of Telegram usernames now. AGENTS.md: collect only the minimum data;
   a username is personal data that can change, can be absent, and identifies a person
   publicly. Adding it needs a privacy note, a retention rule and a migration.
2. Admin UI states the gap once, under the user list: "Telegram username не хранится…".
   No empty username column is drawn. Search stays on internal user ID and Telegram ID.

## Options for the owner (not implemented)
- **A. Keep as is (recommended for the beta).** The tester sends their numeric ID from the
  Mini App (the existing invite flow already asks for it).
- **B. Store the username at login, overwrite on each login.** Needs: nullable column on
  `auth_identities` (or a separate table), Alembic migration, a privacy-policy line, a
  retention rule (delete with the account; ADR-091 account deletion must cover it), no
  logging, case-insensitive index for search, and a test that it is never sent to clients.
  The username can be stale between logins.
- **C. Admin types a note/alias per user.** Admin-entered label instead of Telegram data;
  same deletion and audit rules apply.

## Verification
- `services/api/tests/test_admin_ui_contract.py`: new test that the note is present and no
  username field exists (results in the overnight report).
- No database change, no API contract change.

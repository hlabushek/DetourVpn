# ADR-105: Admin user list shows the linked Telegram ID; sort on the loaded page

Status: implemented overnight 2026-11-11 (API + static admin UI).

## Findings (verified in code)
- Telegram numeric ID is stored in `auth_identities.provider_subject` with provider
  `telegram_bot` (`services/api/src/vpn_api/db/models.py`).
- Telegram username is **not stored** by the API database. No model, migration or handler
  writes it. The bot (`apps/telegram-bot`) may see it, but nothing persists it, so the admin
  panel cannot show it or search by it. Not added: that would need a new data store and a
  privacy review (AGENTS.md: collect only minimum telemetry).

## Decision
1. `GET /api/admin/v1/users` returns `telegram_id` per row: a decimal string, or `null`.
   It is read with one extra parameterized query (`user_id IN (...)`, provider filter), not
   per-row lookups.
2. The admin page shows a "Telegram ID" column only when at least one listed user has one.
3. Sorting (created new/old, Telegram ID, status) runs on the loaded page (max 50 rows). No
   new server-side sort parameter.
4. Search already supports internal user ID (UUID) and Telegram ID (decimal). The placeholder
   text was updated to say so. Username search is not offered.
5. Existing permissions and endpoints are unchanged. The endpoint still requires the admin
   auth check (`admin.authenticate`), and no new telemetry is added.

## Verification
- Static contract tests (`test_admin_ui_contract.py`): pass.
- `test_user_list_exposes_linked_telegram_id_as_decimal_string` in `test_admin.py` needs real
  PostgreSQL (`VPN_TEST_DATABASE_URL`). It is skipped here and was NOT run.

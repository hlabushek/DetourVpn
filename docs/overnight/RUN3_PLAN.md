# Run 3 plan — 2026-11-11 (unattended)

Branch `claude/redesign-devices-servers` verified before work. Nothing is committed, pushed or
deployed. No server, VPS or panel is contacted. Owner's uncommitted files are edited in place.

## Findings that shape the plan (verified in code)
- Telegram username arrives only in the Mini App `initData` `user` JSON
  (`services/api/src/vpn_api/auth/mini_app.py`, `validate_init_data`), and is discarded today.
  The bot code login (`auth/service.py`) carries the numeric subject only.
- Account deletion deletes the `auth_identities` row (`account.py`), so a column there is removed
  with the account (ADR-091).
- Existing secret-at-rest mechanism: Fernet with `VPN_ADMIN_ENCRYPTION_KEY`, already used for
  admin TOTP secrets (`AdminIdentity.totp_ciphertext`). Reused for panel links; no new crypto.
- Windows engine: Amnezia Box `v1.260910.0` (sing-box fork, `third_party/amnezia-box/source-lock.json`),
  built with tags `with_awg,with_gvisor`. Config is generated in `config_factory.cpp`.
- No sample subscription file exists in the repo. Subscription formats cannot be verified offline.

## Order and dependencies
1. ADRs first: 110 (username storage, reverses 109), 111 (panel link import + storage),
   112 (VLESS Reality on Windows engine), 113 (Android gap).
2. A — username: migration `0026_telegram_username` → model → mini-app login update → admin list
   field + search + sort → admin UI → OpenAPI → tests.
3. B1 — `vless://` Reality parser (pure Python, stable error codes) + tests. Subscription parsing
   NOT implemented (open question).
4. B2 — panel server storage: migration `0027_panel_server_sources`, admin import endpoint
   (encrypted link, audited, idempotent), admin list with source column (VPS / panel) → UI → tests.
   Depends on B1.
5. B3 — Windows: `VlessRealityProfile` + `CreateVlessEngineConfig` in `config_factory.cpp` and
   config-generation tests. Not reachable from the UI or the API yet (no delivery path).
6. B4 — Android: Russian message constant and the decision of where it shows; no VLESS core.
7. C — checks: pytest on touched API modules (with throwaway PostgreSQL via
   `scripts/test_with_postgres.py` if it works), ruff, mypy, Windows C++ tests (ctest).
8. Reports: Run 3 sections in `OVERNIGHT_REPORT.md` and `PLAN.md`, `RUN3_OWNER_STEPS.md`.

## Risks
- Username is personal data: never log it, never send it to client apps, delete with account.
- Panel link contains the VLESS UUID (a credential): never log, never return from the API after
  import, never store plaintext. Admin key rotation would make stored links unreadable (same as
  TOTP today) — documented.
- Two new migrations on top of owner's uncommitted `0025`; ordering conflicts if the owner adds
  migrations first.
- The bundled Amnezia Box may lack the VLESS/Reality outbound or its build tags; cannot be proven
  offline without building. Owner step.
- No delivery path from API to client for panel servers in this run: the Windows generator is
  tested in isolation only. No claim that a VLESS connection works.

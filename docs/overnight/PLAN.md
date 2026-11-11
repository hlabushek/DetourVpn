# Overnight plan — 2026-11-11

Branch: `claude/redesign-devices-servers` (worktree `fixes-before-beta-0eeeba`).
Owner's uncommitted work is treated as sacred: no stash/reset/checkout. Files that
already carry owner changes are edited in place and **not committed** (committing
would sweep owner work into our commit). Nothing is pushed or deployed.

## Environment facts (verified)
- Flutter SDK: **not installed** → `dart analyze` / `flutter test` cannot run here.
- Java: 1.8 only → Gradle Android build likely not runnable; to be recorded.
- Python: `.venv` has pytest, ruff, mypy.
- No `version`/update endpoint exists in `services/api` (grep for version/manifest: none).
- No VLESS / Reality / 3x-ui / Remnawave code exists in the repo (only docs).
- Telegram username is **not stored** anywhere in the API DB; only the numeric Telegram ID
  (`auth_identities.provider_subject`, provider `telegram_bot`).

## Order and dependencies
1. T1 FLAG_SECURE removal — independent, small. ADR-103.
2. T6 orbit second tap target — Flutter only, pure-function state resolver + tests. ADR-104.
3. T5 admin panel — Python API (search by Telegram ID, sort) + static UI. Tests in pytest. ADR-105 (only if new query contract).
4. T3 DNS selection — Dart settings + Java/C++ config factory override. Depends on nothing. ADR-106.
5. T7 handshake diagnosis — read-only analysis; fix only if clear and low-risk. Report.
6. T2 in-app update — large. Deliver ADR-107 + manifest schema + validation script + design; Android installer code only if time allows. Partial expected.
7. T4 3x-ui / Remnawave — feasibility ADR-108 + report. No engine change (AGENTS.md: do not fake support).
8. Builds/tests, then OVERNIGHT_REPORT.md and runbook.

## Risks
- Flutter/Dart edits cannot be compiled here → keep them small and state "unverified by compiler".
- Android/Windows tunnel changes need physical-device tests → not provable overnight.
- DNS override changes tunnel config; must not break the default path when no choice saved.
- Update installer = code execution path → hash check, HTTPS only, no downgrade, no key in repo.

## Run 2 (overnight, 2026-11-11)

### Environment correction (verified)
Run 1 said Flutter and a JDK 17+ were missing. That was wrong: they are installed, just not
on `PATH`.
- Flutter 3.47.7 (Dart 3.13.5) at the pinned toolchain path the build script uses
  (`%LOCALAPPDATA%\ProjectVpn\toolchains\flutter`). `where flutter` finds nothing.
- JDK 21 (Android Studio JBR). `java -version` on `PATH` is still 1.8.
- Android SDK 36, Visual Studio 2022 (MSVC + CMake), portable PostgreSQL (main checkout cache).
- `flutter analyze` crashes when run from the OneDrive path with Cyrillic characters; it works
  on an ASCII copy. The project build script already copies sources for this reason.

### Status
| # | Task | Run 2 status |
|---|---|---|
| 2 | In-app update | **Done in code**, tests pass; not verified on a phone; manifest signing open |
| 3 | DNS choice | **Windows wired** (separate config generator); tests pass; not verified on a device |
| 7 | Handshake report | **Message fixed + diagnostic export extended**; full code split deferred (reason in RCA §9) |
| 5 | Telegram username | **ADR-109**, admin page says "not stored"; no new personal data stored |
| — | Builds/checks | All ran and passed (see report); full Android build script exit 0, release APK unsigned; Windows Flutter build not run |

### Order followed
T2 Android side → T3 Windows → T7 → T5 → checks → report. No commits, no deploys, no server access.

## Run 3 (overnight, 2026-11-11)

Full plan with findings and risks: `docs/overnight/RUN3_PLAN.md`. ADRs 110–113 were written
before code.

### Order followed
1. ADRs 110 (username storage, supersedes 109), 111 (panel link import), 112 (Windows VLESS
   generator), 113 (Android gap).
2. A — Telegram username: migration `0026`, model, Mini App login update, admin list/search/sort,
   admin UI, tests.
3. B — `vless://` Reality parser (pure Python) → panel server storage (migration `0027`, encrypted
   with the existing admin Fernet key) → admin endpoints and UI → OpenAPI.
4. B — Windows `CreateVlessEngineConfig` + C++ tests; engine capability checked in the pinned
   source (Reality needs `with_utls`, which the build does not set).
5. B — Android Russian text in `apps/client/lib/panel_servers.dart` + Dart test.
6. C — checks; then reports.

### Not done (by scope or blocked)
- Subscription parsing (formats not verifiable offline), delivery of panel servers to devices,
  engine rebuild with `with_utls` (owner decision), panel server delete, failover (backlog).

# Отчёт I-004 — PostgreSQL и Alembic

Дата: 2026-10-18. Ветка: `codex/i-004-postgres`.

**Локальная I-004 реализована и проверена на настоящем PostgreSQL 17.11.**
Есть connection/transaction layer, две таблицы аккаунтов, первый revision и защищённый
downgrade. Удалённый CI подготовлен, но не запускался. Telegram login остаётся I-005.

## Объём и связь с roadmap

Пользователь поручил следующий этап разработки. I-003 skeleton/health уже создан
в I-001 и проверен повторно; следующий отсутствовавший increment — I-004.
Native SDK gates I-002 не зависят от локальной account DB и остаются открытыми.
Работа ограничена одной I-004; bot/auth/session/RBAC и ledger не реализовывались.

## Изменения

| Файлы | Результат |
|---|---|
| `services/api/src/vpn_api/config.py`, `db/url.py` | Optional SecretStr URL, local-only validation, masked validation text |
| `db/connection.py` | Lazy sync engine, fixed loopback hostaddr, no SQL echo, transaction commit/rollback |
| `db/models.py` | Users и telegram_bot identities, UUID/BIGINT/timezone-aware даты, checks/FK/unique/indexes |
| `alembic.ini`, `services/api/migrations/*` | Explicit upgrade/check/offline DDL и revision 0001_accounts |
| `services/api/tests/test_database_config.py` | 12 config/negative/masking tests |
| `services/api/tests/test_health.py` | 10 health/config tests; включая независимость от недоступной DB |
| `services/api/tests/test_migrations.py` | 11 реальных PostgreSQL cases, включая race conditions |
| `scripts/test_with_postgres.py` | Временный local cluster со случайным паролем и автоматическим stop/cleanup |
| `pyproject.toml`, `uv.lock` | SQLAlchemy 2.1.5, Alembic 1.20.0, psycopg[binary] 3.3.6 и transitive hashes |
| `.github/workflows/backend.yml` | Ephemeral loopback PostgreSQL service для DB tests в будущем CI run |
| `.env.example`, README/runbooks, ADR-011, issue I-004, ROADMAP | Воспроизведение и явно ограниченные локальные допущения |

Публичный OpenAPI не менялся: GET /api/v1/health по-прежнему только liveness.
API не выполняет migrations автоматически, не создаёт accounts по HTTP и не
показывает подключение VPN/авторизацию там, где их нет.

## Как проверено

Тесты основных инвариантов написаны до DB implementation. Зафиксирован red
`ModuleNotFoundError: vpn_api.db`, затем реализованы storage/config/migrations.
Использовалась существующая project venv, CPython 3.13.1, uv 0.11.17, Windows.

Docker Engine недоступен, PostgreSQL binaries в Windows/WSL не обнаружены.
Для тестов из официального EDB HTTPS endpoint получен portable PostgreSQL 17.11;
извлечён только runtime в ignored `.cache/i-004/portable`. Службы Windows,
firewall, системные пакеты/PATH и существующие PostgreSQL clusters не менялись.
Источник и наблюдённый SHA-256 архива:
[runtime receipt](evidence/i-004-postgres-runtime.json).
Это checksum полученных bytes, не независимая vendor signature verification.

Runner создаёт свой cluster в cache, использует random password + SCRAM и loopback
port. Каждый integration case создаёт свою случайную DB. Реальных credentials и
пользовательских данных нет; API/VPS и Telegram не задействованы.
После suite сервер остановлен, cluster удалён. Остатки неудачных первоначальных
запусков отдельно остановлены/удалены после проверки pg_ctl status; ZIP удалён,
portable runtime оставлен для повторения тестов.

| Команда / проверка | Фактический результат |
|---|---|
| `uv lock`, `uv sync --frozen` | PASS: новые versions/hashes установлены в project venv |
| `uv lock --check` | PASS |
| `uv run --frozen ruff check .` | PASS |
| `uv run --frozen ruff format --check .` | PASS |
| `uv run --frozen mypy` | PASS: 15 source/test/migration/script files |
| `uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin` | PASS: **33 passed, 0 skipped** на PostgreSQL 17.11; временный сервер остановлен |
| `uv run --frozen python scripts/export_openapi.py --check` | PASS, контракт не изменился |
| `uv run --frozen alembic heads` | PASS: 0001_accounts, один head |
| `uv run --frozen alembic upgrade head --sql` | PASS: offline PostgreSQL DDL; отдельно от реальных tests |
| `uv run --frozen pre-commit validate-config` | PASS |
| Export locked requirements + `pip-audit --disable-pip --require-hashes` | PASS: No known vulnerabilities found |
| Workflow YAML/service assertions | PASS: loopback port и isolated test settings; не remote Actions run |
| Dev `docker compose ... config --quiet` | PASS с validation-only env value; контейнеры не запускались |

Отдельные доказанные инварианты:

- Empty upgrade, повторный upgrade и Alembic check без расхождения с ORM.
- Timezone-aware timestamps, pending по умолчанию, нет автоматической verified_at.
- FK, provider/status/positive BIGINT checks, user/identity uniqueness и индексы.
- Исключение в transaction context не оставляет вставленную строку.
- Два конкурентных insert одного Telegram subject дают одну identity, второй отказ.
- Empty downgrade/reupgrade работает; непустой downgrade сохраняет rows и revision.
- Downgrade ждёт concurrent writer до проверки пустоты; commit новой строки
  приводит к отказу downgrade, а не к удалению только что записанных данных.
- PGHOSTADDR из окружения не перенаправляет connection за пределы configured loopback.

Первоначальный Windows harness обнаружил особенности кодировки и наследования
stdout pipes у дочернего PostgreSQL. Исправлено: вывод control commands в файл,
bounded timeouts, explicit stop и проверенный cleanup. После исправлений suite
пройдена полностью; оставленных работающих test servers нет.

## Риски и ограничения

- Config validator разрешает только local-only increment; production TLS/network
  roles/backup/restore не реализованы. Loopback не гарантирует отсутствие SSH-forward.
- SQLAlchemy hide_parameters не очищает PostgreSQL DETAIL; DB exceptions нельзя
  целиком публиковать/логировать. В I-004 нет пользовательского DB endpoint.
- `verified_at` и status — данные, не доказанная authentication/authorization logic.
  Регистрация, bot Updates, pairing, OTP consume и RBAC/MFA относятся к I-005.
- Downgrade с данными намеренно запрещён. Данные нельзя удалять ради отката;
  нужен forward migration или заранее проверенный isolated restore plan.
- CI test service с trust ограничен ephemeral loopback runner; dev Compose
  по-прежнему требует пароль. Настоящий GitHub Actions run не выполнен.
- Тесты на Linux/Docker не выполнялись. PostgreSQL-specific поведение проверено
  на Windows portable 17.11; CI config для Linux подготовлен.
- pip-audit — только известные Python package advisories в текущей среде;
  не аудит portable PostgreSQL DLL, OS или будущего tunnel SDK.

Runbook: [database.md](runbooks/database.md). Следующая issue — I-005,
авторизация кодом из Telegram-бота по ADR-001 и правила административного доступа.

Финальная проверка состава: 10 обновлённых и 16 новых файлов, 5 локальных ссылок
валидны, эвристический secret scan без findings. AGENTS, TECHNICAL_SPEC,
стартовый prompt, пользовательский PGN и OpenAPI сохранены. `.env` не создавался;
test cluster directories и процессы project PostgreSQL отсутствуют.

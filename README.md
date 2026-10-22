# Project VPN — вход через Telegram, устройства и учёт квоты

I-019 (2026-10-22): локальный выбор одного из двух operator-configured узлов и
явное завершение истёкшего lease при потере узла. При неизвестных final counters
списывается полный резерв; клиент объясняет это до recovery. Реальный failover
между двумя VPS ещё не принят: нужен отдельный одобренный staging drill.
[Настройка и drill](docs/runbooks/node-failover.md),
[проверки](docs/verification-i-019.md), [ADR-031](docs/adr/031-node-failover.md).

I-017 (2026-10-21): реализован локальный Telegram Mini App в стиле Soft Orbit:
баланс, бонусы/кликер, устройства и история. Сервер проверяет initData, выдаёт
ограниченную сессию и использует общий ledger. По умолчанию выключен; реальный
Telegram/HTTPS запуск ещё не проверен. [Настройка](docs/runbooks/telegram-mini-app.md),
[результаты проверок](docs/verification-i-017.md), [решения](docs/adr/029-telegram-mini-app.md).

I-012 **технически принят 2026-10-20 в bounded staging scope (ADR-024)** — key confirmation,
quota reservation, native peer add/remove, конфиг после ACK и kernel budget/expiry.
На VPS и физическом Pixel прошли API revoke, actual agent restart и expiry при
потере control-plane transport; подтверждены cutoff, settlement, network restore
и cleanup. [Фактический receipt](docs/evidence/i-012-staging-lifecycle.json).
Без trusted staging profile admission остаётся закрытым.
[Повторное воспроизведение](docs/runbooks/tunnel-lifecycle-staging.md),
[проверки и границы](docs/verification-i-012-admission.md).

Работает локальный HTTP API: `GET /api/v1/health` → `{"status":"ok"}`.
Это проверка жизни процесса, работающая без БД/Redis. Реализованы локальные API
авторизации через Telegram-код, сессии, registry устройств и quota ledger на PostgreSQL.
Production VPN, fleet accounting и платежи ещё не реализованы. Flutter MVP описан
в I-013/I-014; I-012 измеряет native final counters ограниченного staging lease.
I-011 содержит Android native POC с реальным handshake и egress
на физическом Pixel 8 Pro через staging; проверены IPv6-проба, смена сети,
disconnect и cleanup/deadline. Индивидуальный peer revoke с прекращением
трафика проверен; технический I-011 принят в границах [ADR-022](docs/adr/022-i011-acceptance-boundary.md).
[Результаты](docs/evidence/i-011-revoke-staging.json).
Сборка и ограничения: [Android POC](docs/runbooks/android-poc.md).

I-007 добавляет остаток/историю квоты в целых байтах, однократное стартовое начисление
и внутренние credit/debit операции с durable replay protection. Сумма старта пока
не выбрана: VPN_INITIAL_QUOTA_BYTES по умолчанию 0. Публичных методов самоначисления
нет. [Квота и сверка](docs/runbooks/quota.md), [проверки I-007](docs/verification-i-007.md).

I-008 добавляет внутреннюю обработку cumulative rx/tx: привязки node/peer к владельцу,
явные epochs/reset, replay/late reports, pending при нехватке квоты, atomic ledger
debit и сверку. [Инструкция](docs/runbooks/usage.md), [проверки I-008](docs/verification-i-008.md).
Публичного приёма счётчиков нет; node identity/transport остаются I-009. Local CLI
работает с доверенными данными в собственной тестовой БД и не является настоящим агентом.

Путь входа: начать попытку в клиенте → открыть бота по ссылке → получить код
в личном чате → ввести в клиенте → работать со своими устройствами → выйти/отозвать
доступ. Telegram webhook adapter и sendMessage реализованы, но **настоящий бот
не подключён**: токен и одобренный HTTPS ingress настраиваются вручную локально.
Сценарий проверен автоматическими тестами с настоящей БД и fake sender только в tests.
Email/OIDC в этот flow не входят. [Ручная проверка](docs/runbooks/authentication.md).

## Запуск на Linux Mint

Команды выполняются в корне репозитория. Нужен `uv 0.11.17`, закреплённый в
pyproject.toml. Если его нет: `pipx install uv==0.11.17`; сам pipx устанавливается
отдельно средствами вашей системы. Системный Python не заменяется.

```bash
uv python install 3.13
uv run --frozen uvicorn vpn_api.main:create_app --factory --app-dir services/api/src --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

Вторая команда создаёт `.venv`, устанавливает зависимости из `uv.lock` и запускает API.
В другом терминале: `curl --fail http://127.0.0.1:8000/api/v1/health`. Остановка: Ctrl+C.
На Windows с Python 3.13/uv команда запуска та же; проверка в PowerShell:
`Invoke-RestMethod http://127.0.0.1:8000/api/v1/health`.
Linux-команды документированы; фактическая проверка этого изменения проведена на Windows.

`.env` необязателен; пример — `.env.example`. HTTP access log выключен, чтобы не
сохранять IP и произвольные URL запросов. Swagger/OpenAPI по HTTP выключены;
для локальной разработки можно задать `VPN_ENABLE_API_DOCS=true` в `.env`.
Схема хранится в `packages/contracts/openapi.json`. Production-конфигурации нет.

## Проверки

```bash
uv lock --check
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen pytest
uv run --frozen python scripts/export_openapi.py --check
```

Без VPN_TEST_DATABASE_URL PostgreSQL integration tests явно пропускаются.
Полный запуск с временным настоящим сервером и команды миграций:
`docs/runbooks/database.md`. Итог auth/devices: `docs/verification-identity-stage.md`.

После изменения контракта: `uv run --frozen python scripts/export_openapi.py`.
Git hooks: `uv run --frozen pre-commit install`.
CI выполняет эти проверки, проверку Compose и dependency audit. Настройка CI
подготовлена; удалённый результат появится только после публикации в приватный GitHub.
Локальные результаты: `docs/verification-i-001.md`.

## Структура

| Путь | Назначение сейчас |
|---|---|
| `services/api/src/vpn_api` | Health/errors, Telegram pairing, sessions, RBAC gate, devices, PostgreSQL |
| `services/api/migrations`, `alembic.ini` | Accounts/authentication/devices/quota, guarded downgrade |
| `services/api/tests` | API, ownership, negative/concurrency, real PostgreSQL; Telegram HTTP mocked explicitly |
| `packages/contracts` | Генерируемый OpenAPI |
| `apps/android` | I-011 native POC: physical handshake/egress/revoke приняты; публичный выпуск закрыт release gates |
| `apps/telegram-mini-app` | I-017 React/TypeScript кабинет; запуск через same-origin API, без публикации |
| `apps/admin` | README, исполняемого UI нет |
| `services/worker`, `services/node-agent` | README, исполняемого агента нет |
| `infra/compose` | Необязательные локальные PostgreSQL/Redis |
| `infra/ansible` | README, развёртывание не реализовано |
| `docs/adr` | Product/native ADR, local persistence, auth/session/retention и device ownership |
| `scripts/local_auth_client.py` | Ручной локальный клиент; без сохранения/вывода кодов и токенов |
| `scripts/auth_cleanup.py` | Явная очистка истёкших auth записей |
| `services/api/src/vpn_api/quota` | Immutable ledger/receipts, read API и reconciliation |
| `scripts/quota_reconcile.py` | Read-only сверка квоты без вывода идентификаторов |
| `services/api/src/vpn_api/usage`, `scripts/usage_local.py` | Trusted usage accounting и local ingest/audit |

Review: `docs/architecture-review.md`. Исследование I-002 выполнено:
`docs/verification-i-002.md`, `docs/licensing-checklist.md`, `docs/compatibility-matrix.md`.
ADR остаются Proposed; native build/release gates открыты, SDK не интегрирован.
Текущий статус и оставшиеся условия: `docs/issues/I-002.md`. Инфраструктура и матрица сред:
`docs/runbooks/local-development.md`. Соглашения: `docs/contributing.md`.
Зависимости и лицензии: `docs/dependencies.md`.

I-004: [инструкция БД](docs/runbooks/database.md), [отчёт](docs/verification-i-004.md).
I-005/I-006 реализованы локально: [запуск](docs/runbooks/authentication.md),
[контракт auth](docs/adr/012-authentication-implementation.md),
[устройства](docs/adr/013-device-ownership.md), [отчёт](docs/verification-identity-stage.md).
Admin MFA enrollment отсутствует, административные операции закрыты. Local shared
limiter использует PostgreSQL; Redis пока не подключён. До реальных пользователей
нужны privacy/account deletion/recovery и infrastructure gates. I-007 quota ledger
реализован локально, I-008 usage engine также готов к local review;
следующий issue I-009 — node registry и authenticated agent contract.

AGENTS и CODEX_START_PROMPT сохранены. TECHNICAL_SPEC v0.2 и ROADMAP обновлены
2026-10-18 по требованию владельца о входе кодом из Telegram-бота; причина записана в ТЗ.
Исходный README перенесён в `docs/source/startup-kit-readme.md`, чтобы корневой
README описывал фактический запуск. Бренд и лицензия нашего кода не выбраны.
Репозиторий остаётся локальным.

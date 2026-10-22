# Отчёт I-001

Дата: 2026-10-15. Ветка: `codex/i-001-foundation`. Исходная папка была пустым
Git-репозиторием без commits/remotes. Локальная реализация подготовлена;
удалённый репозиторий и GitHub Actions пока не проверены.

## Что создано и зачем

- `services/api/src/vpn_api/{main,config,schemas}.py`: FastAPI app factory,
  local/test settings, health, серверный request_id и error envelope 404/405.
- `services/api/tests/test_health.py`: 9 проверок HTTP/конфигурации, включая
  неподдерживаемый метод/маршрут, недоверенный request_id и invalid environment.
- `pyproject.toml`, `uv.lock`, `.python-version`: точные direct/transitive версии,
  hashes, Python baseline, Ruff/mypy/pytest; `.pre-commit-config.yaml` и CI workflow.
- `.env.example`, `.gitignore`, `.gitattributes`, `.editorconfig`: локальные
  настройки без secrets, исключения данных/ключей и соглашения по файлам.
- `scripts/export_openapi.py`, `packages/contracts/openapi.json`: фактический
  контракт и проверка его актуальности.
- README компонентов `apps/*`, `services/worker`, `services/node-agent`,
  `infra/ansible`: обозначенные будущие части без фиктивной реализации.
- `infra/compose/compose.yaml`: необязательные PostgreSQL/Redis, loopback ports,
  pinned digests, healthchecks, запрет пустого пароля PostgreSQL.
- `docs/architecture-review.md`: 14 замечаний с приоритетами и точками решения;
  три ADR Proposed, licensing/dependency note, runbook, conventions, текст I-002.

Исходные AGENTS/TECHNICAL_SPEC/ROADMAP/CODEX_START_PROMPT совпадают с архивом побайтно.
Исходный README сохранён побайтно в `docs/source/startup-kit-readme.md`; новый
корневой README описывает фактическое состояние и запуск. Смысл ТЗ не менялся.

## Среда и фактические проверки

Windows, PowerShell, CPython 3.13.1, uv 0.11.17. Новая проектная `.venv`;
системные пакеты/настройки, Docker daemon и внешняя инфраструктура не менялись.

| Команда / проверка | Результат |
|---|---|
| `uv lock`, `uv sync --frozen` | PASS: lock создан, зависимости установлены в .venv |
| `uv lock --check` | PASS: lock соответствует pyproject |
| `uv run --frozen ruff format --check .` | PASS |
| `uv run --frozen ruff check .` | PASS |
| `uv run --frozen mypy` | PASS: 6 Python source/test/script files, strict mode |
| `uv run --frozen pytest` | PASS: 9 tests |
| `uv run --frozen python scripts/export_openapi.py --check` | PASS: схема актуальна |
| `uv run --frozen pre-commit validate-config` | PASS; hooks подготовлены, автоматически не установлены |
| Workflow YAML parser + assertions | PASS: события, job/steps, contents:read; это не remote Actions run |
| `docker compose --env-file .env.example -f infra/compose/compose.yaml config --quiet` с временным validation-only env value | PASS, exit 0; сервисы не запускались |
| Та же команда с пустым значением из .env.example | Ожидаемый отказ до запуска, exit 1 |
| Audit locked requirements по командам runbook | PASS: `No known vulnerabilities found` для применимых пакетов в этой среде |
| Отдельный процесс `uv run --frozen uvicorn ... --host 127.0.0.1 --port <свободный> --no-access-log`, Python urllib | PASS: настоящий HTTP GET health 200/status=ok/request_id и POST 405/error.code |
| Завершение smoke | PASS: дерево созданных процессов остановлено, временный слушающий порт закрыт |
| Byte comparison с ZIP | PASS: все пять исходных документов сохранены |
| Heuristic scan repo text и git check-ignore | PASS: поддерживаемых паттернов secrets нет; env/venv/keys/DB исключены |

Audit command: экспорт с hashes в `.venv/audit-requirements.txt`, затем
`uv run --frozen pip-audit --requirement .venv/audit-requirements.txt --disable-pip --require-hashes`.
Проверка искала private-key headers, GitHub/AWS/Telegram token patterns; эвристика
не является доказательством отсутствия любых возможных секретов. Реальные secrets
не запрашивались, `.env` не создавался. Файлы не публиковались и не отправлялись на VPS.

Тесты написаны до реализации. Первый запуск выявил deprecation HTTPX в текущем
Starlette, после замены на закреплённый HTTPX2 зафиксирован ожидаемый red
`ModuleNotFoundError: vpn_api`, затем реализация дала 9 passing tests.
Первый одноразовый smoke подтвердил HTTP, но не завершил дочерний процесс uv;
он явно остановлен, проверка повторена с завершением всего созданного дерева и
проверкой закрытия порта. Оставленного работающего API нет.

## Что не подтверждено и какие риски остаются

- Docker CLI установлен, Docker Engine недоступен (нет named pipe Linux engine).
  Контейнеры, DB/Redis connectivity, persistence и health в работающей среде не проверены.
- Linux Mint запуск документирован, фактической проверки на Mint не было.
- GitHub repo/remote/PR/issues не созданы; workflow подготовлен, Actions не запускался.
  Поэтому удалённая часть DoD I-001 остаётся открытой. Автоматический commit не сделан.
- Нет Android build/SDK, auth, ledger, tunnels, payments, node agent или deploy.
  Health не является readiness этих компонентов и не подтверждает VPN/egress.
- ADR-001/002/003 Proposed. SDK/engine versions и лицензия нашего проекта не утверждены.
  Native compatibility/licensing и offline quota enforcement остаются ключевыми blockers.
- Dependency audit не покрывает CPython/OS/Docker image vulnerabilities и не заменяет
  external security review/SBOM. Внешние требования/лицензии перепроверяются перед release.
- I-001 API возвращает только health и routing errors. Расширение к 401/403/422/500,
  production telemetry, HTTPS и readiness требует отдельной задачи.

## Дальнейшая задача

`docs/issues/I-002.md` содержит готовый текст и критерии приёмки. Она не начата.
Отдельно нужно согласовать перепланирование I-003: минимальный health уже сделан
по прямому стартовому заданию комплекта. Другие roadmap issues не реализовывались.

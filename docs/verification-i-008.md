# I-008 — local usage ingestion: результат и проверка

2026-10-18. Реализован внутренний accounting path: trusted binding → explicit
counter epoch → cumulative report → atomic quota debit/pending → audit.
Node registry/credentials/collector/transport и настоящий VPN сюда не входят.

## Что изменилось

- UsageStream фиксирует (node UUID, peer UUID) → device/user; owner берётся из
  device, composite FK запрещает cross-owner связь. Binding не переназначается.
  Node UUID пока accounting identifier, **не доказательство source authentication**.
- Epoch открывается trusted control action с expected_generation/CAS. Counter
  reset нельзя оформить произвольным sample. Первый report списывает rx+tx от
  нулевого начала; первые bytes не теряются на бесплатном baseline.
- Versioned schema v1 ограничивает integer bytes, combined BIGINT range, sequence,
  timezone-aware sampled_at и UUID event key. Нет owner/node override, URLs/DNS/
  payload/ключей. Public HTTP ingest/admin routes не добавлены.
- Durable sample receipts защищают key+payload и (epoch, sequence). Повтор возвращает
  тот же результат, изменение payload/key прежнего события вызывает conflict.
- Более старый уже покрытый report — stale/no extra charge; регресс rx или tx,
  времени/неверный interval/future report — rejected без сдвига watermarks.
- Полученные и уже списанные counters хранятся отдельно. Blocked debit не теряет
  pending bytes; новый валидный report покрывает весь pending delta. Старый retry
  не начинает списывать после пополнения. Новый epoch не обнуляет старый pending.
- Sample, quota receipt/ledger debit и mutable watermarks в одной transaction под
  User → stream → epoch locks. Разные peers одного аккаунта делят admission lock.
- Исторические sample receipts содержат arrival order и epoch_end_at_receipt:
  последующее закрытие периода не меняет прежнюю причину отказа при audit.
- Migration 0005 создаёт 3 таблицы и usage-only debit source, FK/unique/check/index
  constraints и immutable sample guards. Непустой downgrade запрещён.
- Local CLI: bind/open-epoch/ingest/audit. Он требует local DB access, читает
  максимум 16 KiB и не является node auth/mTLS или production mock adapter.

## Фактические результаты

Windows, Python 3.13.1, portable PostgreSQL 17.11. Последний полный suite:
**220 passed, 0 skipped, 139.02 seconds**. 157 прежних + 63 новых usage checks.
Все DB tests использовали настоящий disposable PostgreSQL и отдельные случайные
test databases; постоянная БД пользователя не изменялась. Runner остановил сервер
и удалил cluster; после прогона postgres.exe и cluster directories отсутствовали.

| Проверка | Результат |
|---|---|
| Ruff format / lint | Passed |
| mypy strict | No issues in 47 source files |
| uv lock --check | Passed, 73 packages; версии/hashes не менялись |
| Public OpenAPI --check | Current |
| Usage sample/result JSON Schema --check | Current |
| pip-audit frozen hashes | No known vulnerabilities found на момент запуска |
| Ограниченный credential-pattern source scan | 0 candidates; значения не печатались |
| Separate-process CLI ingest/retry/audit | Passed с real disposable PostgreSQL |

Проверены все 6 permutations трёх reports, same-key/concurrent cumulative input,
cross-peer shared quota, reset/late retired epoch, reset-versus-report race,
blocked/replay/refill/final-snapshot debt settlement, persistent watermarks после
перезапуска сервиса, timestamp/counter regressions, strict ranges/aware datetime,
точность выше 2^53, unknown node/epoch, binding ownership/FK, отсутствие public
mutation route, disabled/revoked final accounting, immutable receipts, atomic
failure после ledger insert и outer rollback. CLI invalid/oversized input не
отражает body; forensic projection corruption даёт audit exit 1 без repair.
Empty downgrade/reupgrade/ORM drift сохраняют прежние quota данные.

Synthetic counters и public-key-shaped bytes существуют только в tests/manual
test input, не являются native keypair или actual traffic evidence. Общие auth
fixtures по-прежнему используют явно FakeTelegramSender. Реальных сообщений,
node connections, network captures или VPN payload не было.

### Regression, обнаруженная при review

Первый полный прогон 212 checks прошёл. Дополнительный review обнаружил, что
закрытие epoch позже rejected report могло изменить его причину при audit,
если заново использовать текущую границу периода. Исправлено сохранением
epoch_end_at_receipt; regression test включён в финальные 220. Дополнительные
cross-stream/CAS/rollback/CLI scenarios тоже вошли в финальный полный прогон.
Необработанных test failures в финальном результате нет.

### Команды из корня

```powershell
uv lock --check
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python scripts/export_openapi.py --check
uv run --frozen python scripts/export_usage_contracts.py --check
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
uv export --frozen --no-emit-project --format requirements-txt --output-file .venv/audit-requirements.txt --quiet
uv run --frozen pip-audit --requirement .venv/audit-requirements.txt --disable-pip --require-hashes
```

С другим disposable local PostgreSQL: VPN_TEST_DATABASE_URL + pytest, см. database.md.
Обычный pytest без него пропускает DB tests и не является полной проверкой.
Workflow получил usage contract check; remote GitHub Actions run не выполнялся.

## Основные файлы

| Группа | Пути |
|---|---|
| Domain | services/api/src/vpn_api/usage/{contracts,service}.py и __init__.py |
| Storage | db/models.py; migrations/versions/0005_usage_ingestion.py |
| Quota integration | quota/service.py и quota/contracts.py |
| Tests | tests/test_usage.py, test_usage_edges.py; migration expectations |
| Tooling | scripts/usage_local.py, scripts/export_usage_contracts.py |
| Contracts/CI | packages/contracts/usage-sample.v1.json, usage-result.v1.json, openapi.json; .github/workflows/backend.yml |
| Documentation | ADR-004, issue I-008, runbook usage.md, README/ROADMAP и related docs |

Новых dependencies нет. AGENTS.md, TECHNICAL_SPEC.md, CODEX_START_PROMPT.md и
chessy_training_game.pgn не редактировались. Branch codex/i-008-usage-ingestion.
Репозиторий остаётся без baseline commits/remote, файлы untracked: это локальные
артефакты для review, не опубликованный PR/release.

## Ручные действия и ограничения

Для своего local test окружения применить `alembic upgrade head` и `alembic check`,
используя credentials только в игнорируемом .env. CLI и формат report подробно
описаны в runbooks/usage.md. Binding требует существующего собственного test device;
синтетические reports не записывать в рабочий учёт. Отдельные source IDs из CLI
не заменяют service authentication. Постоянная БД и VPS не менялись.

Учёт позднего расхода существующего binding возможен после disable/revoke,
чтобы он не исчезал. Это не открывает новый epoch/peer/session. Нельзя считать
этот debit доказательством выполнения revoke на узле: I-012 ещё впереди.
Accounting и audit O(history), production нагрузка/SLO не проверены. Receipts/
epochs/bindings пока хранятся на время аккаунта; retention/compaction/deletion,
least-privilege runtime DB roles, backup/restore и source auth нужны до реальных данных.

**Следующий issue — I-009:** node registry, capabilities/version checks и
authenticated agent contract. Реальный collector и доставка ещё не подключены;
hard cutoff, overspend bound/leases, physical Android VPN, Docker runtime,
native SDK/licence clearance и production deployment этим этапом не подтверждаются.

# I-007 — учёт квоты: результат локальной разработки

2026-10-18. Реализован законченный accounting increment: once-only initial grant
при code login → свой остаток и история → внутренние credit/debit → сверка.
User-facing write API отсутствует. Это не получение реального трафика с VPN узла.

## Реализованное поведение

- Integer bytes без float: signed BIGINT, точные суммы и range checks до 2^63-1.
  Доступно = granted − used; debit целиком либо durable insufficient_quota, без
  отрицательного остатка/partial spending. used здесь — учтённые debits.
- Immutable quota_ledger как источник истины. Нет изменяемого balance cache.
  quota_operations хранит исходный payload и applied/rejected outcome. Повтор
  key+payload возвращает тот же result/balance snapshot, даже после других операций.
  Изменение payload и новый key прежнего business source отвергаются.
- Accepted и rejected receipts транзакционны; отказ при недостатке квоты не станет
  списанием после будущего пополнения. User lock сериализует конкурирующие writers.
- Default initial quota = 0 до выбора размера владельцем. Серверная настройка
  VPN_INITIAL_QUOTA_BYTES; canonical registration receipt один/user. Нулевой receipt
  также обработан once-only. Повтор login/другое устройство/смена настройки не
  дают повторный бонус. Несколько Telegram accounts не равны одному человеку:
  этот механизм не решает Sybil/антифрод.
- GET /api/v1/quota и /quota/history owner-only, no-store; immutable sequence и
  snapshot pagination. Внешние IDs/query parameters не выбирают другого владельца.
- Read-only quota_reconcile.py проверяет все prefixes/receipts/totals и возвращает
  exit 0/1/2 с aggregate counts без account/Telegram IDs, SQL и credentials.
- Migration 0004: две таблицы, FK/index/unique/check constraints, guards против
  UPDATE/DELETE/TRUNCATE. Непустой downgrade запрещён, включая rejected receipts.

## Фактические проверки

Windows, Python 3.13.1, portable PostgreSQL 17.11. **157 passed, 0 skipped,
98.48 seconds** в последнем полном прогоне. Из них 67 новых проверок квоты и 90
предыдущих auth/devices/foundation checks. Все DB tests — настоящий PostgreSQL,
созданы отдельные случайные БД в disposable cluster; сервер остановлен, cluster
удалён. Проверено отсутствие оставшихся postgres.exe и временных cluster directories.

| Проверка | Итог |
|---|---|
| Ruff format/check | Passed |
| mypy strict | No issues in 39 source files |
| uv lock --check | Passed, 73 packages; dependencies/lock не менялись |
| OpenAPI export --check | Current |
| pip-audit frozen export/hashes | No known vulnerabilities found на момент проверки |
| Credential-pattern source scan | 0 candidates (ограниченные bot token/private PEM/password URL patterns) |
| Настоящий CLI reconcile, согласованная test DB | Exit 0, корректные counts |
| Настоящий CLI reconcile, forensic corrupted test DB | Exit 1, расхождение не исправлено |

Особенно проверены:

- accepted/rejected replay после других операций; key/source conflict;
  same-key concurrency и два различных списания одного остатка;
- mixed concurrent writes и три набора generated operation sequences против
  отдельной Python integer model; initial grant concurrency и repeated login;
- точность выше 2^53 в core и JSON API, BIGINT overflow, rejection float/bool/string;
- atomic rollback receipt/ledger и всего auth+quota при synthetic database failure
  до session credential insert: код после rollback можно безопасно погасить снова;
- ownership/no public mutation, revoked bearer, stable history snapshot,
  DB composite ownership FK и guards против всех трёх видов mutation;
- missing ledger receipt, tampered prefix/head, nonempty rollback (включая только
  rejected receipt), empty downgrade/reupgrade, schema drift и cleanup guards.

Synthetic SQL failure triggers и forensic отключение guard существуют только в
disposable tests; production repair/force paths не добавлены. Telegram sender
в auth fixtures явно FakeTelegramSender. Реальных Telegram сообщений не было.
Ни синтетические ledger debits, ни passed tests не доказывают реальный traffic.

### Воспроизведение из корня

```powershell
uv lock --check
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python scripts/export_openapi.py --check
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
uv export --frozen --no-emit-project --format requirements-txt --output-file .venv/audit-requirements.txt --quiet
uv run --frozen pip-audit --requirement .venv/audit-requirements.txt --disable-pip --require-hashes
```

При другом disposable PostgreSQL — VPN_TEST_DATABASE_URL и pytest, см. database.md.
Без этого обычный pytest пропускает DB tests и не является полной проверкой.
Финальные checks прошли; необработанных test failures нет. Live Telegram, Docker,
remote CI, физический Android и VPN data plane этим не проверялись.

## Файлы

| Группа | Изменённые/новые файлы |
|---|---|
| Domain/contracts/read API | services/api/src/vpn_api/quota/{contracts,service,routes}.py, __init__.py |
| Schema | db/models.py; migrations/versions/0004_quota_ledger.py |
| Auth integration / error / permissions | auth/service.py, auth/security.py, config.py, main.py |
| Tests | test_quota.py, test_quota_api.py; migrations/auth fixtures/conftest |
| Manual tooling | scripts/quota_reconcile.py; scripts/local_auth_client.py |
| Public contract | packages/contracts/openapi.json |
| Docs/config | .env.example; README/ROADMAP; ADR-014; issue/runbook/report I-007; auth/database/dependency docs |

Новых библиотек нет. AGENTS.md, TECHNICAL_SPEC.md, CODEX_START_PROMPT.md и
chessy_training_game.pgn не редактировались. Текущая branch codex/i-007-quota-ledger.
Репозиторий всё ещё без baseline commits/remote, файлы untracked: это локальные
артефакты, не опубликованный PR/release и не remote CI evidence.

## Ручные действия и ограничения

В постоянную пользовательскую БД изменения не применялись. Для своего local
окружения выполнить migration, задать размер initial quota в игнорируемом .env
и перезапустить API; подробности: runbooks/quota.md. Code login через настоящий
test bot требует прежних отдельных HTTPS/webhook/secret настроек; они не сделаны.

SUM/reconcile O(history), production throughput не измерен. Быстрый API guard
проверяет totals/head/receipt count, полный prefix audit — ручная команда.
DB owner может отключить triggers; least-privilege roles и backup/restore/privacy
retention нужны до staging/real accounts. Immutable rows/receipts пока хранятся
на время аккаунта; account deletion/anonymisation не реализованы.

Это завершённый I-007 внутри roadmap phase 2. **I-008 usage ingestion ещё впереди**:
trusted node/peer/epoch/sequence, replay/reset/late-sample policy и связь с ledger.
Реальный VPN cutoff, overspend bounds/leases, node agent и tunnel SDK/physical-device
проверки отсутствуют. Admin MFA/Redis/privacy/recovery gates phase 1 также остаются;
никаких admin mutation routes в обход MFA этим increment не открыто.

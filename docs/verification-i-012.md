# I-012 — первая локальная реализация lifecycle

**Исторический отчёт первой поставки.** Продолжение с key proof, reservation,
native agent и kernel lease: [текущие проверки](verification-i-012-admission.md).
Описанные ниже ограничения относятся к первой поставке, не к последнему коду.

Дата: 2026-10-19. Windows, Python 3.13.1, uv 0.11.17.
Ветка: `codex/i-012-tunnel-lifecycle`. **Issue остаётся открытым.**

## Реализовано

- Owner-only create/list/read/delete запросов туннеля; create требует сессию,
  привязанную к своему активному device. 202 означает `admission_pending`.
  Конфигурация не выдаётся, автоматического provisioning нет.
- Durable receipt `(user_id, idempotency_key)` на записи запроса, conflict
  параметров и один незавершённый запрос на device. Повтор возвращает тот же
  ID в текущем состоянии, включая результат отзыва.
- Отмена никогда не выданного peer — `revoked`. Для назначенного peer — durable
  outbox `remove_peer`, `revoke_pending` до точного authenticated ACK.
- Отзыв устройства/сессий и его tunnel/outbox выполняются одной транзакцией.
- Versioned removal poll/ack: node identity, nonce/time, capability, command ID,
  peer ID/version. Повтор доставки/ACK безопасен; stale ACK отклоняется.
- Migration 0007, FK ownership/node, unique/partial indexes и downgrade guard.
  Нет разрешённого `active` или `add_peer`, переключателя обхода admission,
  новых библиотек/секретов, native/Android изменений или remote side effects.

## Изменённые файлы

- `services/api/src/vpn_api/tunnels/{__init__,contracts,service,routes}.py`:
  исполняемые контракты, операции и HTTP API.
- `services/api/src/vpn_api/{main,devices}.py`, `auth/security.py`: routers,
  permission и атомарный device revoke.
- `services/api/src/vpn_api/db/models.py`,
  `services/api/migrations/versions/0007_tunnel_lifecycle.py`: две новые таблицы.
- `services/api/tests/test_tunnels.py`, `test_tunnels_closed.py`,
  `test_migrations.py`: lifecycle, negative/concurrency и schema regression.
- `scripts/export_usage_contracts.py`, `packages/contracts/openapi.json`,
  `packages/contracts/peer-{poll,commands,ack,ack-result}.v1.json`: экспорт схем;
  существующий CI check теперь проверяет и peer schemas. Internal endpoints
  по-прежнему исключены из public OpenAPI.
- `docs/adr/023-tunnel-admission-and-revocation.md`, `docs/runbooks/tunnels.md`,
  `docs/issues/I-012.md`, этот отчёт, `ROADMAP.md`, `README.md`,
  `services/node-agent/README.md`: решение, воспроизведение и честный статус.

## Выполненные проверки

| Команда | Фактический результат |
|---|---|
| `uv lock --check` | Passed, lock не изменён |
| `uv run --frozen ruff format --check .` | Passed |
| `uv run --frozen ruff check .` | All checks passed |
| `uv run --frozen mypy` | Success, 67 source files |
| `uv run --frozen python scripts/export_openapi.py --check` | Current |
| `uv run --frozen python scripts/export_usage_contracts.py --check` | Usage/peer contracts current |
| `uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin` | **354 passed**, 168.97 s, без skips |

Целевой запуск migrations/tunnels: **25 passed**, 329 deselected. После полного
запуска расширена существующая DB constraint проверка на запрет `active`/
`add_peer`; её повтор: **1 passed**, 353 deselected. Производственный код после
полного запуска не менялся. В каждом прогоне runner создал изолированный
настоящий loopback PostgreSQL, остановил его и удалил только свой cluster.

В 14 новых tests проверены: owner/foreign/missing resource, bound/unbound/revoked
session, request replay/conflict, concurrent create и create/device revoke,
cancel/recreate, device revoke/ACK race, durable outbox после restart API,
повтор удаления/ACK, чужой node, malformed/version/capability/nonce, stale ACK,
rollback outbox/nonce, ownership/node FK и guarded populated downgrade.
Общие migration tests проверили upgrade/repeat, Alembic schema drift,
пустой downgrade/reupgrade и отказ downgrade при concurrent account writer.

Первый полный прогон: 348 passed, 3 failed. Ошибка отмены запроса была вызвана
autoflush промежуточного `revoked` до заполнения timestamp; получение DB времени
перенесено до изменения состояния. Осталась также старая ожидаемая revision в
concurrent downgrade test — обновлена до 0007. Повторные целевые и полный прогоны
прошли. Неразрешённых test failures нет.

## Что этими проверками не доказано

Telegram sender — явно fake только в tests. Назначенные peers, их состояния
и ACK — **synthetic fixtures**, не реально установленные peers. Приложение после
restart проверялось новым TestClient/ORM sessions с той же БД, не аварийным kill
процесса. Tests не подключались к VPS, не устанавливали agent и не создавали VPN.
Новая миграция применялась только к disposable test DB. Существующая dev DB не
менялась; необходимые ручные команды — в runbooks/tunnels.md.

Публичного ready config, add_peer, native executor/daemon, inventory reconciliation,
key ownership proof и bounded quota lease пока нет. Node ACK означает заявление
аутентифицированного узла; проверка реального удаления/прекращения трафика требует
отдельного стенда. Offline cutoff, quotas и отсутствие сетевых утечек не заявлены.
Production retention/cleanup, credential rotation/HTTPS ingress и release gates
не закрыты. Android не менялся; Gradle/physical-device tests не запускались.
Удалённый CI, GitHub publication, deployment и реальные пользовательские сценарии
этим отчётом не подтверждаются. В репозитории по-прежнему нет первого commit;
существующие untracked файлы не добавлялись в commit и не публиковались.

## Следующая работа

Продолжать **I-012**: утвердить/реализовать proof и quota reservation/lease с
измеримым пределом перерасхода; verified node profile и add_peer/executor;
readiness/config и полную inventory reconciliation. Затем подготовить конкретный
bounded staging план и получить отдельное одобрение перед remote side effects,
проверить add/revoke/replay/restart/offline/lease на узле и Android.
Это требование AGENTS.md и ADR-016; I-013 не начинать до закрытия I-012.

Из ранее записанных out-of-scope дефектов документации сохраняется формулировка
ADR-008 о «проверенной подписи»: I-009 использует bearer credential. В этой issue
ADR-008 не изменялся; новый runbook прямо описывает реальный механизм.

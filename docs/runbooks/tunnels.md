# I-012: локальные запросы и durable revoke

Это инструкция первой поставки, без admission. Текущий bounded staging путь и
единственная команда финальной приёмки: [tunnel-lifecycle-staging](tunnel-lifecycle-staging.md).

Дата: 2026-10-19. Реализована первая часть I-012, admission закрыт.
Решение: [ADR-023](../adr/023-tunnel-admission-and-revocation.md).

## Подготовка

Нужны локальный PostgreSQL и настроенный auth из authentication.md. Секреты
задать локально, не передавать в чат или аргументы команд. Для своей dev DB:

```powershell
uv sync --frozen
uv run --frozen alembic upgrade head
uv run --frozen alembic check
```

Новая revision — `0007_tunnel_lifecycle`. Команды не выполняются автоматически
на существующей dev/staging DB. Тестовый runner создаёт только временные БД.
Миграция не создаёт пользователей, узлы, назначения, credentials или peer.

## Пользовательский контракт

После входа зарегистрировать свой public key через `/api/v1/devices`, тем самым
привязать сессию к устройству. Private key остаётся на клиенте.

- `POST /api/v1/tunnels`: JSON `device_id`, `idempotency_key` (UUID), auth Bearer.
  Возвращает 202, `admission_pending`, ID, device ID, version и timestamps.
  **Никакой конфигурации и готового VPN нет.** Автоматического перехода к выдаче
  сейчас нет; не использовать эти запросы как live очередь provisioning.
- Повтор с тем же ключом возвращает тот же объект в текущем состоянии. Другой
  device с тем же ключом — `tunnel_idempotency_conflict`/409. Второй живой запрос
  для того же device — `tunnel_exists`/409.
- `GET /api/v1/tunnels`: только свои записи, `limit` 1–100 и `offset` 0–100000.
  `GET /api/v1/tunnels/{id}`: только свой объект, чужой/неизвестный — `not_found`/404.
- `DELETE /api/v1/tunnels/{id}`: 200 с текущим состоянием. Для никогда не выданного
  peer — `revoked`. Для назначенного узла — `revoke_pending` до подтверждения узла.
  Повтор не создаёт новую команду. После завершения можно создать запрос с новым key.
- `DELETE /api/v1/devices/{id}`: прежний 204 отзывает устройство/сессии и теперь
  атомарно отменяет его запросы/создаёт outbox. Это не обещание немедленного
  прекращения data-plane трафика. С другой сессии владельца читать status туннеля.

Дополнительные ошибки: `unauthorized`/401, `session_device_mismatch`/409,
`device_revoked`/409, `invalid_request`/422, `rate_limited`/429,
`auth_unavailable`/503, `storage_unavailable`/503. Сообщения не включают inputs.
Ответы содержат `Cache-Control: no-store`, server-generated `X-Request-ID`.

## Узловой контракт удаления

Это исполняемые HTTP endpoints control plane, **не установленный node daemon**.
Они не предоставляют `add_peer` или назначение узла. Текущий public create
не создаёт node-bound запись. Назначенные peers в тестах только synthetic:
не создавать их вручную в эксплуатационной БД для обхода admission gates.

`POST /api/internal/v1/agent/peers/poll`:

```json
{"contract_version": 1, "capabilities": ["peer-removal.v1"], "limit": 100}
```

Заголовки I-009: `X-Agent-Node-Id`, `X-Agent-Credential`, `X-Agent-Timestamp`,
`X-Agent-Nonce`. Только локальный тест; перед remote ingress нужны отдельное
одобрение staging, HTTPS, rate limit и lifecycle credential. Poll возвращает
свои незавершённые `remove_peer` с command_id, peer_id, peer_version, public_key.
Повтор poll после restart API возвращает те же durable команды. Не удалять peer
по произвольным body fields и не реализовывать shell endpoint.

`POST /api/internal/v1/agent/peers/ack`: version 1, capability `peer-removal.v1`,
command_id, peer_id, peer_version, outcome `removed`. Каждый HTTP retry требует
нового nonce, но сохраняет идентификаторы команды. Неподтверждённое удаление
не ack-ать. `peer_version_conflict`/409 оставляет команду pending; чужая/неизвестная
команда даёт `not_found`/404, replay nonce — `agent_replay`/409, неверная версия —
`agent_version_unsupported`/409, credential/time — `agent_unauthorized`/401.
Unknown capability или outcome — `invalid_request`/422.

Полная inventory reconciliation, daemon/native executor, подтверждённое node
применение и admission/lease/proof принадлежат оставшейся I-012. Offline node
не позволяет обещать revoke deadline. БД хранит только заявление доверенного
узла: live data-plane проверяется отдельно на одобренном стенде.

## Проверка и откат

```powershell
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python scripts/export_openapi.py --check
uv run --frozen python scripts/export_usage_contracts.py --check
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
```

Обычный pytest без `VPN_TEST_DATABASE_URL` пропускает PostgreSQL tests. Runner
выше проверяет настоящую БД, но не Telegram или VPN. Две новые таблицы проверяются
на schema drift, FK ownership/node, unique/index constraints, upgrade/reupgrade
и защищённый downgrade. Downgrade непустой схемы запрещён и откатывается целиком;
не удалять историю запросов/команд ради downgrade. Перед эксплуатацией нужны
backup/restore и согласованная retention/deletion policy.

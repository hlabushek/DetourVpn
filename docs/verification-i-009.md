# I-009: локальная проверка реестра узлов и agent v1

## Что реализовано

- Реестр узлов хранит label/region/status/contract version и digest случайного
  256-bit credential. Trusted provisioning принимает UUID idempotency key, выдаёт
  credential только один раз, а повтор с теми же параметрами возвращает прежний
  node id без повторной выдачи секрета.
- `POST /api/internal/v1/agent/usage` принимает только contract version 1 и текущий
  usage sample schema v1. Узел выбирается по credential header, не по request JSON.
- Timestamp ограничен ±60 секундами; UUID nonce одноразовый для узла. Nonce и usage
  receipt/debit коммитятся в одной PostgreSQL транзакции. Повтор sample с новым nonce
  возвращает прежний receipt благодаря I-008 idempotency.
- API ошибки не отражают body/credential. Внутренний agent route исключён из public
  OpenAPI; его JSON envelope описан в `packages/contracts/agent-usage.v1.json`.

## Проверки и результат (2026-10-18)

- `uv run ruff check` — passed.
- `uv run mypy` — passed, 54 files.
- `uv run python scripts/export_openapi.py --check` — passed.
- `uv run python scripts/export_usage_contracts.py --check` — passed.
- `uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin`
  — **224 passed** against disposable local PostgreSQL; runner остановил сервер и
  удалил созданный test cluster.
- PostgreSQL tests include migration upgrade/downgrade/schema check, idempotent node
  provisioning, wrong-credential and replay rejection, end-to-end authenticated usage
  debit, and same-receipt retry without a second debit.

## Границы и безопасность

- Synthetic rows/counters only. Не было реального агента, VPS, VPN-туннеля, трафика,
  DNS/egress проверки или проверки quota hard cutoff.
- Credential отправляется как bearer secret: удалённый ingress обязан использовать
  HTTPS; сам HTTP deployment и TLS ingress не настроены. Reverse proxy должен удалять
  эти заголовки из логов. Credential rotation/revocation admin flow не реализованы.
- Nonce rows пока не удаляются и будут расти; retention/cleanup нужен до staging.
- Per-node rate limit не включён; удалённый ingress должен ограничивать частоту запросов
  до появления измерений нагрузки.
- Provisioning доступен только через trusted internal service, не через публичный API.
  Перед выдачей узлам нужен закрытый operator workflow, secret delivery и RBAC/MFA.
- Downgrade миграции 0006 разрешён только при пустых `vpn_nodes` и
  `agent_request_nonces`; непустые данные сохраняются при отказе.

## Воспроизведение

Из корня репозитория запустите команды выше. Для прямого PostgreSQL тестирования
укажите путь к уже подготовленным локальным `initdb.exe`/`pg_ctl.exe`; runner не
скачивает и не устанавливает сервер. Никогда не подставляйте production URL.

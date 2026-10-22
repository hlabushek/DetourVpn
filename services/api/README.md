# Control API

I-001: `vpn_api.main:create_app`, `GET /api/v1/health`. Запуск из корня по README.
`status=ok` означает только ответ HTTP-процесса; БД, Redis, узлы и VPN не проверяются.
Health не вызывает внешние сервисы. X-Request-ID генерируется сервером; ошибки
имеют стабильный envelope без отражения кодов/токенов/SQL/входного JSON.

I-004 добавляет db/models.py, local-only engine/transaction и Alembic account schema.
Health не подключается к storage. Реальные PostgreSQL tests уже
проверяют transaction rollback, identity uniqueness и downgrade races. Вызовы БД
из auth/device HTTP endpoints добавлены в I-005/I-006. Runbooks в корне:
docs/runbooks/database.md и docs/runbooks/authentication.md.

Authentication opt-in через VPN_ENABLE_AUTH. По умолчанию auth routes fail closed
503, health доступен. Production mode не поддерживается. При включённом auth только
доверенные webhook Updates могут инициировать отправку кода настоящим TelegramSender;
никакого production fake-кода/ключей нет. Tests используют явно FakeTelegramSender.
Перед ручным подключением нужны свой test bot и одобренный HTTPS ingress, см. runbook.
API device registration получает только public key. Реального tunnel provisioning нет.

I-007 добавляет quota/routes.py и quota/service.py, immutable ledger/receipts и
миграцию 0004. GET /quota и /quota/history доступны только owner; публичных mutation
routes нет. Успешный code login атомарно выдаёт once-only initial quota, default 0
до выбора размера владельцем. Read-only CLI: scripts/quota_reconcile.py.
Принципы/границы — docs/adr/014-quota-ledger.md, запуск — docs/runbooks/quota.md.
Сбор счётчиков с настоящего node collector пока не подключён.

I-008 реализует internal usage engine и local CLI, а не public ingestion endpoint.
Миграция 0005, schema v1, scopes/epochs/cumulative rx+tx и atomic usage debit.
Синтетические reports проверены на настоящем PostgreSQL; реального node collector
ещё нет. I-009 добавляет internal authenticated endpoint v1 и node registry; он
использует synthetic checks, реального node collector ещё нет. Детали:
docs/verification-i-009.md и docs/adr/008-node-agent-contract.md.
# I-015: rewards

Authenticated `GET /api/v1/rewards/rules` and `POST /api/v1/rewards/claim`
use the same PostgreSQL ledger as quota. No economic defaults are seeded.
Configuration, replay/caps and rollback: `docs/runbooks/rewards.md`.
Current acceptance limits: `docs/verification-i-015.md`.


# I-018: referrals

Authenticated `/api/v1/referrals`, `/code`, `/resolve`, `/claim` provide
owner-scoped referral campaigns and pull-based qualification. Migration 0011
seeds no campaigns. Only settled, observed traffic with an applied matching
quota debit qualifies; campaign budget/cap and ledger credit are atomic.
Configuration/rollback: `docs/runbooks/referrals.md`; decisions: ADR-030.

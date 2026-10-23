# I-022 — ручные подписки без оплаты

Это backend модель и authenticated read API. Полноценная выдача через UI/MFA —
I-023, работа подписки с длительным многопользовательским dataplane — I-024.
Не выдавать эти этапы за завершённый ежедневный VPN.

Локальная dev DB: `uv run --frozen alembic upgrade head` применяет 0012 и
распределяет старый остаток по credit FIFO без изменения журнала. Перед важной
БД — encrypted backup/изолированный restore, остановить writers, проверить объём
и время backfill. Downgrade при funding/plan/subscription истории откажет.
Closed DB: отдельный миграционный job из closed-control-plane.md.

`GET /api/v1/plans` — последние revisions, деньги в копейках, quota bytes;
`GET /api/v1/subscriptions` — последние 100 собственных выдач с точной версией
плана, периодом, состоянием и остатком. Оба требуют обычный auth Bearer.
Запись через эти endpoints отсутствует, кнопок покупки и payment orders нет.
Каталог пуст до явного admin setup; dev/demo users не создаются автоматически.

`GET /quota` добавляет `unavailable_bytes`: revoked/expired/scheduled quota.
`available=granted-used-reserved-unavailable`. История сохраняет все credit,
измеренный/консервативный settlement не меняется из-за истечения срока.
Истечение не создаёт ложный traffic debit и не меняет ledger revision.
Состояние квоты надо обновлять по времени: неизменный revision не доказывает
неизменность доступной квоты. Android parser понимает поле и показывает его
отдельно от использованного и зарезервированного трафика.

Политика: ADR-035. Изменение тарифа создаёт новую revision. Продление — новая
будущая выдача, addon действует до конца текущей основной подписки. Без основной
активной подписки addon не выдать. Отмена будущей или отзыв текущей выдачи —
immutable событие, не DELETE. Не переносить остатки прямой SQL правкой.
Изменения должны проходить через MFA admin adapter (I-023); импорт Python
сервиса не является эксплуатационной авторизацией.

До I-024 сервер вернёт `entitlement_requires_peer_protocol_v3` при новом legacy
подключении аккаунта с подписками. Действующий legacy tunnel нужно отозвать и
дождаться settlement перед выдачей первой подписки, иначе
`subscription_legacy_tunnel_active`. Обычный прежний staging без подписок
сохраняется. Не снимать этот gate ради демонстрации connect.

Проверки:

```powershell
$env:PYTEST_ADDOPTS = '-q services/api/tests/test_subscriptions.py services/api/tests/test_quota.py services/api/tests/test_migrations.py --tb=short'
.venv/Scripts/python.exe scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Это настоящий временный PostgreSQL; данные и даты синтетические, не VPN/payments.

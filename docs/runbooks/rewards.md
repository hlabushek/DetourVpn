# Награды DetourVpn — I-015

Применить миграции: `.venv\Scripts\python.exe -m alembic upgrade head` в локальном
окружении с настроенной `VPN_DATABASE_URL`. Затем обычный запуск API по existing
runbook. Секреты только из окружения; значения не выводить в терминал.

Без конфигурации `GET /api/v1/rewards/rules` возвращает пустой items.
Стартовый трафик не меняется. Rewards UI появится в Mini App I-017; обычное
приложение показывает награды только как учтённые записи истории трафика.

Ручная настройка после утверждения себестоимости: через параметризованную
операторскую транзакцию БД создать по одному `reward_rules` на `daily` и
`clicker`: UUID id, integer reward_bytes, daily_cap_bytes >= reward_bytes,
daily_cap_claims >= 1 (daily строго 1), cooldown_seconds >= 1, enabled,
starts_at/ends_at как timezone-aware timestamps. Окно полуоткрытое [start,end).
Готовых production чисел намеренно нет. Тип существующей строки не менять;
для отключения установить enabled=false. Обновление суммы/лимита не стирает
накопленные claims. Перед production rollout нужны отдельное согласование,
экономические замеры и backup; эта issue внешнюю инфраструктуру не меняет.

Client request: `POST /api/v1/rewards/claim` с bearer token и JSON
`{"rule_id":"<UUID>","idempotency_key":"<новый UUID>"}`. Клиент не передаёт bytes,
user_id, время или тип награды. Успех — receipt со снимком начисленной суммы и
баланса после операции (до резерва). Актуальный available читать из `/quota`.

Повтор успеха тем же ключом возвращает тот же receipt; другой rule с этим ключом
даёт `reward_idempotency_conflict`. Ошибки `reward_unavailable`,
`reward_limit_reached`, `reward_cooldown`, `quota_capacity_exceeded` не создают
claim. Auth/rate/storage ошибки используют общий structured error envelope.
Повтор сетевой ошибки — прежние rule_id/key. Rate limit: 120 requests/min/account,
3000 globally; shared GET/POST bucket. Нет raw IP, дополнительного fingerprinting
или URL/DNS telemetry. Rate buckets обслуживает существующий auth cleanup.

История: `/quota/history`, source_type=`reward`; source_id указывает claim UUID.
Сверка: существующий quota reconciliation плюс claim.operation_id → operation,
проверить user/key/source_id, amount, credit/applied и ровно одну ledger row.
Claims — append-only, срок хранения как у accounting ledger; перед production
нужен общий retention/privacy gate. Никакого удаления наград при закрытии UI.

Rollback: остановить writers, backup. `alembic downgrade 0008_tunnel_admission`
допускается только до первого reward claim/operation. При наличии начислений
миграция отказывает; использовать forward fix, не удалять финансовый журнал.

Проверки:

```powershell
.venv\Scripts\python.exe -m ruff format --check services/api
.venv\Scripts\python.exe -m ruff check services/api
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe scripts/export_openapi.py --check
$env:PYTEST_ADDOPTS = '-q --tb=short -k "rewards or quota or migrations"'
.venv\Scripts\python.exe scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
.\scripts\build_flutter_client.ps1
```

Flutter tests используют явно TEST FAKE API; серверные tests — настоящую disposable
PostgreSQL и TEST FAKE Telegram delivery. Это не проверка туннеля или egress.
На физическом телефоне: открыть историю после начисления через authenticated
test caller и сверить history/баланс с БД. API-тестами проверить receipt/replay,
отказ второго daily, cooldown и clicker cap. Полный UI claim/retry сценарий Mini App
проверяется в I-017. Экономические параметры для такого теста синтетические и не
должны включаться в production.

Автоматизированный physical-сценарий (один разблокированный Android с разрешённой
USB-отладкой, после обычной main.dart сборки):

```powershell
.\scripts\run_i015_android.ps1
```

Это отдельный local-only режим existing I-013 harness, без staging opt-in.
В disposable PostgreSQL создаётся TEST ONLY daily rule на 17 байт; телефон
вызывает настоящий authenticated `/rewards/claim`, повторяет ключ и проверяет
ровно одну строку «Бонусный трафик» в quota/history и неизменный баланс после
повтора/refresh. Test-only verify сверяет claim, ledger и reconciliation в БД.
Санитизированный результат — `docs/evidence/i-015-local-android.json`; нельзя
считать сценарий пройденным без passed=true и cleanup flags. Общий harness
останавливает API/приложение, снимает временный app-op и ADB reverse; PostgreSQL
runner удаляет кластер. Обёртка сохраняет и восстанавливает обычный debug APK
и устанавливает его обратно на телефон даже при неуспешном тесте. Не вызывать
после ручной сборки test entrypoint вместо обычного main.dart APK.

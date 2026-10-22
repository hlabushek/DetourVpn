# Локальная квота и сверка ledger

Нужен собственный local PostgreSQL по database.md. Сервер/токены/Telegram не
нужны для автоматических тестов. Команды выполнять из корня проекта.

## Миграция и стартовая квота

В игнорируемом `.env` локально задать VPN_DATABASE_URL; настройки ручного входа —
authentication.md. Credentials не передавать в чат/history. VPN_INITIAL_QUOTA_BYTES
принимает целое число байт 0..9223372036854775807. Default 0: размер пока не выбран;
пример ответа из ТЗ не является тарифом. Ненулевое значение определяет владелец.
Остановить свой local API и применить схему:

```powershell
uv sync --frozen
uv run --frozen alembic upgrade head
uv run --frozen alembic check
```

Revision 0004_quota_ledger не начисляет всем пользователям квоту автоматически.
При успешном code login создаётся один registration receipt и credit/user, включая
zero receipt при default 0. Изменение настройки не переоценивает уже обработанный
аккаунт. Старый аккаунт с прежней действующей сессией может увидеть пустой ledger
и initial_grant_applied=false до следующего code login. GET/refresh не начисляют.

Запустить API по README и ручной клиент:

```powershell
uv run --frozen python scripts/local_auth_client.py
```

Для настоящего тестового бота предварительно нужны ручные HTTPS/webhook настройки
из authentication.md. Клиент скрывает код/токены и после входа показывает
granted/used/available в байтах. Он не создаёт тестового трафика или произвольных
начислений. Публичного credit/debit API нет.

## API и внутренние операции

GET /api/v1/quota возвращает granted_bytes, used_bytes, available_bytes, revision,
initial_grant_applied и reserved_bytes (I-012). Доступно к новым операциям:
`available_bytes = granted_bytes - used_bytes - reserved_bytes`. Исторические
ledger balance_after остаются `granted - used`, reservation не является debit.
На settlement reservation закрывается и окончательный debit пишется атомарно.
used_bytes — сумма учтённых debits, не показание клиента
или доказательство реального трафика; node counters будут в I-008.
GET /api/v1/quota/history — свои applied ledger entries. Limit 1..100. Для следующей
страницы сохранить snapshot_revision и передать next_before_sequence как
before_sequence: новые записи не попадут в прежний snapshot. Owner берётся только
из bearer session. Ответы no-store. JSON суммы — integers; будущий JS-клиент должен
сохранять точность выше 2^53, не преобразовывать их через Number.

LedgerService предназначен только для доверенных backend workflow. LedgerCommand:
kind credit/debit, positive amount_bytes, UUID source_id и UUID idempotency_key.
Source — конкретное business event, не произвольная пользовательская метка. При retry
оба UUID стабильны. Тот же key+payload возвращает старый итог/balance snapshot;
изменение payload или подмена key прежнего source отвергаются. Отказ debit тоже
сохраняется: после пополнения старый rejected command остаётся rejected.

apply коммитит/откатывает одну transaction; apply_in_transaction участвует в общей
transaction auth/workflow. Подтверждать результат внешнему вызывающему только после
commit. Прямые INSERT вне сервиса не поддерживаются. Correction — новая операция;
privileged workflow/admin API ещё нет, MFA gate не обходить.

## Сверка без изменения данных

```powershell
uv run --frozen python scripts/quota_reconcile.py
```

Выводит JSON counts: accounts, inconsistent_accounts, violations, ledger_entries,
receipts. Идентификаторы и secrets не печатаются. Для одного local account UUID
есть --user-id (это не Telegram ID). Credentials читаются из .env/окружения.

- Exit 0: проверенные chains и receipts согласованы.
- Exit 1: найдены расхождения, данные не исправлены.
- Exit 2: target отсутствует либо ошибка configuration/schema/storage; согласованность не подтверждена.

Проверяются sequence, signed running balances, balance_after, receipt/payload,
accepted/rejected outcomes и totals. Берётся User lock по одному аккаунту: writes
этого аккаунта могут ждать. Весь проход не является global snapshot. SUM/reconcile
O(history); production throughput не доказан. API проверяет totals/head/receipt
count; полный prefix audit выполняет команда. Это не защита от DB owner, способного
отключить triggers. При расхождении нужен reviewed restore/forward correction;
не менять/удалять rows для «исправления» остатка. Автоматического repair нет.
Ledger/receipts пока хранятся на время аккаунта; deletion/anonymisation и backup
retention должны быть определены до реальных пользователей.

## Откат и проверки

При остановленном своём API пустую quota schema можно откатить:

```powershell
uv run --frozen alembic downgrade 0003_devices
```

Непустой ledger или receipts (включая rejected/zero) вызывают отказ под locks.
Не удалять rows ради downgrade. Нужны forward migration либо проверенный restore.
После downgrade вернуть upgrade или прежний код: I-007 fail-closed требует таблицы.

Полный прогон на отдельном disposable PostgreSQL:

```powershell
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
```

Тестовые amounts/events синтетические. Node/peer/epoch/sequence/reset/late samples
и реальное ограничение VPN этим не подтверждаются. Следующий issue — I-008.

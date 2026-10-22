# Локальная обработка cumulative traffic reports

Это accounting engine и trusted local CLI, не работающий VPN node или защищённый
HTTP transport. Автоматические тесты создают собственные synthetic bindings/reports
в disposable PostgreSQL; настоящие Telegram token/VPS/Android для них не нужны.

## Полная проверка

Из корня проекта, с уже подготовленным portable runtime:

```powershell
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
uv run --frozen python scripts/export_usage_contracts.py --check
uv run --frozen python scripts/export_openapi.py --check
```

Другой local disposable PostgreSQL — VPN_TEST_DATABASE_URL и pytest, как в database.md.
Без этого DB tests пропускаются; обычный pytest не подтверждает корректность ingest.

## Собственная local test DB

В игнорируемом .env указать VPN_DATABASE_URL своего local test PostgreSQL. Credentials
не передавать в чат/history. Остановить свой API и выполнить:

```powershell
uv run --frozen alembic upgrade head
uv run --frozen alembic check
uv run --frozen python scripts/usage_local.py --help
```

Migration 0005 создаёт usage_streams, usage_epochs, usage_samples и разрешает в quota
source=usage только debit. Она не создаёт node/device/binding, не подключается к
VPS и ничего не начисляет. Исходные auth/device/quota rows сохраняются.

## Provisioning и ingest из trusted caller

Для существующего собственного test device создать binding: node/peer UUID —
отдельные accounting identifiers, которые будущий I-009 должен связать с проверенной
node principal. Пользовательский JSON не может выбрать node/owner/device.
В командах ниже заменить placeholders локальными IDs, без секретов:

```powershell
uv run --frozen python scripts/usage_local.py bind --node-id "<node-uuid>" --peer-id "<peer-uuid>" --device-id "<device-uuid>"
uv run --frozen python scripts/usage_local.py open-epoch --node-id "<node-uuid>" --peer-id "<peer-uuid>" --epoch-id "<epoch-uuid>" --expected-generation 0 --started-at "<timezone-aware-ISO-time>"
```

Open epoch допустим только при доказанном нулевом начале/reset counters. Перезапуск
коллектора при продолжающихся counters не создаёт новый epoch автоматически.
Node agent должен сохранять generation/epoch/sequence и idempotency keys/outbox
между перезапусками; его реализация ещё впереди. Для смены epoch expected_generation
равен текущему generation, started_at строго больше прежнего начала и не раньше
уже observed reports. Повтор тех же параметров не активирует старый epoch заново.

Пример **синтетического** report (только отдельная test DB, не рабочий учёт),
заменить UUID/time; время внутри epoch, UTC offset обязателен:

```json
{
  "schema_version": 1,
  "peer_id": "<peer-uuid>",
  "counter_epoch": "<epoch-uuid>",
  "sequence": 1,
  "sampled_at": "<timezone-aware-ISO-time>",
  "rx_total_bytes": 100,
  "tx_total_bytes": 50,
  "idempotency_key": "<stable-event-uuid>"
}
```

Сохранить локально в игнорируемой tmp/ (никаких payload/URLs/DNS/keys в JSON):

```powershell
uv run --frozen python scripts/usage_local.py ingest --node-id "<node-uuid>" --sample-file tmp/usage-sample.json
```

CLI читает максимум 16 KiB; не выводит malformed payload/SQL/DB URL. Ответ — status,
sample_id, attempted delta_bytes, pending_bytes и quota_operation_id, без owner ID.
Первая запись потребляет rx+tx от нуля. Retry сохраняет точно те же key/sequence/
payload. Изменение payload при key или новый key для прежнего sequence — conflict.
Время более 60 секунд в будущем, уменьшение counters и неконсистентный stale report
отвергаются durable receipt. Исправленный report получает новые sequence/key.

Статусы:

- charged: delta отражён в quota ledger, charged watermark передвинут.
- blocked: ledger сохранил отказ; observed передвинут, charged остался прежним.
- no_change: нулевой delta, фиктивного debit нет.
- stale: более старый report уже покрыт observed snapshot, дополнительного debit нет.
- rejected: некорректная progression/time, watermark не передвинут.

Недостаток квоты не забывает bytes. Повтор blocked возвращает старый отказ даже
после пополнения. Новый report с большим sequence покрывает весь ещё не списанный
delta. Для retired epoch нужно новое final-snapshot событие со старыми counters/
measurement time внутри того epoch, новым sequence/key; новое поколение не стирает
старый pending. Частичных debit/отрицательного остатка нет. Peer accounting может
завершаться после disable/revoke; это не выдаёт новое подключение/сессию/epoch.

## Сверка и откат

```powershell
uv run --frozen python scripts/usage_local.py audit
```

Проверяет epochs, arrival order, observed/charged/pending, outcomes и связи с quota
receipts, затем quota ledger prefixes. Историческая граница epoch сохраняется в
receipt, поэтому последующая смена поколения не меняет прежние решения.
Вывод — aggregate accounts/streams/epochs/samples/pending_bytes/violations.
Exit 0 — согласовано; 1 — расхождения без исправления; 2 — configuration/storage/input
error. Аккаунты проверяются по одному под User lock; это не global snapshot и
может задержать writes. Никаких автоматических repair/force/truncate flags нет.

Пустой usage layer можно откатить при остановленном своём API/collector:

```powershell
uv run --frozen alembic downgrade 0004_quota_ledger
```

Любой binding/epoch/sample или quota receipt source=usage запрещает downgrade.
Не удалять данные ради rollback. Для данных — reviewed forward migration либо
проверенный backup/restore; текущий код после downgrade требует вернуть head.

До real collection нужны I-009 node auth/registry, least-privilege DB/transport,
production retention/deletion/backup policy и нагрузочная проверка. Данные пока
хранятся для жизни аккаунта, без автоматического cleanup/compaction. Этот increment
не доказывает live node delivery, bounded VPN overspend, SLO или physical tunnel:
leases/enforcement и настоящий Android/node adapter ещё не реализованы.
## I-012: отдельный учёт аренды туннеля

Managed lease peer из I-012 нельзя дополнительно привязать к I-008: bind вернёт
`usage_managed_by_lease`. Lease settlement имеет source `tunnel_lease` и
проверяется по reservation, а I-008 source `usage` — по sample/epoch receipts.
Это предотвращает двойное списание и ложную ошибку общей сверки.


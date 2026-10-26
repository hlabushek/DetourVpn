# I-016 — локальные backup, восстановление и оповещения

Решения/риски: ADR-028. Это CLI для локального PostgreSQL, не HTTP endpoint.
Запускать из корня проекта. API, VPN/Android, VPS и внешние аккаунты не нужны.

## I-027: одноразовая beta maintenance команда

`scripts/beta_maintenance.py` объединяет retention cleanup аутентификации и
quota ledger reconciliation под PostgreSQL advisory lock. Команда не создаёт
расписание и не отправляет уведомления. Загрузить локальные `VPN_*` настройки
из secret manager и выполнить:

```powershell
uv run --frozen python scripts/beta_maintenance.py
```

Exit 0 — запуск завершён и inconsistency не найдена; exit 1 — найдена
inconsistency; exit 2 — ошибка конфигурации/БД; exit 3 — другой экземпляр уже
работает. При сбое часть cleanup/reconciliation могла завершиться; повторный
запуск безопасен. Вывод агрегирован и не содержит account IDs. Этот runner не
продлевает пользовательские сессии, не закрывает kernel peer и не заменяет
проверку tunnel lease expiry. Для beta он должен запускаться из одобренного
supervisor, контролирующего пропущенные запуски; сам supervisor пока не
установлен.

## Автоматическое воспроизведение

```powershell
$env:PYTEST_ADDOPTS = '-q services/api/tests/test_operations.py'
.\.venv\Scripts\python.exe scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Runner создаёт изолированный PostgreSQL на случайном loopback port, выполняет
настоящие dump/encrypt/restore/reconcile и удаляет созданный кластер. Данные
синтетические, Telegram/VPN не используются. Другой локальный binary bundle
можно указать через `VPN_TEST_POSTGRES_BIN` и `--postgres-bin` (один и тот же
каталог). Использовать доверенные PostgreSQL binaries совместимой major версии
(для этой приёмки bundled PostgreSQL 17.11); обновления — по отдельному audit gate.

## Ручная локальная эксплуатация

1. Создать `secrets/` и `backups/` на локальном диске вне общих/синхронизируемых
   каталогов. Примеры ниже используют ignored каталоги репозитория только для
   dev. На Windows заранее ограничить ACL текущим оператором и SYSTEM; POSIX
   каталог 0700, файлы 0600. Не полагаться на POSIX mode на Windows.
2. Загрузить `VPN_OPERATIONS_DATABASE_URL` в окружение процесса из своего
   локального secret manager. Не писать URL в команду, stdout, screenshot,
   issue или чат. `.env` намеренно не читается. Разрешены только literal
   `127.0.0.1`/`::1`, без query options, обычное имя БД до 63 ASCII символов
   (буквы/цифры/underscore, начинается с буквы/underscore). libpq connection
   strings вместо имени БД запрещены. Не использовать production credentials.
3. Backup role: CONNECT и SELECT на все необходимые таблицы/последовательности;
   не superuser. Read-only monitor использует SELECT на schema version,
   vpn_nodes и peer_commands, плюс чтение backup/key и диска. Изменений БД нет.
4. Drill role требует CREATEDB/владение своими targets. Запускать исключительно
   на disposable cluster без ценных данных; SQL из trusted dump исполняется
   PostgreSQL. Не восстанавливать сторонние архивы даже с известным ключом.
   Для отдельного drill cluster задать его local URL; backup source binding
   monitor относится к исходной БД, а не к drill cluster.

Однократно сгенерировать ключ (существующий файл не перезаписывается):

```powershell
.\.venv\Scripts\python.exe scripts/operations.py keygen --key-file secrets/backup.key
```

Создать **новое** имя архива при каждом запуске (предыдущий не перезаписывается):

```powershell
.\.venv\Scripts\python.exe scripts/operations.py backup --key-file secrets/backup.key --archive backups/local-001.fernet --postgres-bin .cache/i-004/portable/pgsql/bin
.\.venv\Scripts\python.exe scripts/operations.py drill --key-file secrets/backup.key --archive backups/local-001.fernet --postgres-bin .cache/i-004/portable/pgsql/bin
.\.venv\Scripts\python.exe scripts/operations.py check --key-file secrets/backup.key --archive backups/local-001.fernet --disk .
```

Backup: exit 0 + `backup_created`. Drill: exit 0 + `drill_passed`, tables и число
проверенных accounts; БД `vpn_i016_drill_<random>` удаляется до success output.
Все public tables сверяются целиком, включая auth/usage/reservations/rewards,
затем проверяется existing ledger reconciliation каждого аккаунта. Ключи и
identity значения не выводятся. После команды удалить secret env variable.

Check: exit 0 = только перечисленные local checks прошли; exit 1 = alerts;
exit 2 = ошибка самой команды. Сбой backup/drill тоже exit 2 (`operations_failed`)
без сырого SQL/libpq stderr. Не считать старый архив новым после такой ошибки.
Наличие `drill_passed` не означает восстановленные production API/VPN: сервисы
не запускаются, credentials из копии не активируются. Это не full disaster recovery.

## Коды и действия

| Код | Следующее действие оператора |
|---|---|
| database_check_failed | Проверить локальную DB connectivity и Alembic head; не считать HTTP 200 проверкой БД |
| node_inventory_empty | Узлы не зарегистрированы; проверить ожидаемую конфигурацию |
| node_heartbeat_stale | Active node не присылал authenticated receipt 90s, timestamp NULL или в будущем; проверить agent/clock/network |
| peer_command_stalled | Pending command не ACK 90s; проверить агент и reconciler, не удалять ledger/reservations вручную |
| backup_unavailable | Нет читаемой аутентичной копии/ключа, неверный формат или превышен лимит; проверить файлы без печати содержимого |
| backup_stale | Возраст >24h или timestamp в будущем; проверить clock/scheduler и создать новую копию |
| backup_source_mismatch | Архив другой БД/порта/пользователя; выбрать правильную копию |
| disk_space_low | На указанном локальном диске свободно <1 GiB; проверить capacity |
| disk_check_failed | Диск недоступен для проверки; исправить path/permissions |

Каждый check выдаёт актуальный набор alerts. Исчезновение кода на следующем
успешном check — recovery. Не проверяются Redis, работа Telegram, внешний API,
туннель/egress, bandwidth удалённого узла, WAN, usage ingestion SLA или платежи.
Local disk check ничего не говорит о свободном месте на VPS.

## Расписание, доставка и retention

CLI одноразовый; в этой issue scheduler не установлен и уведомления никому
не отправляются. Для будущего одобренного staging: backup хотя бы раз в сутки,
drill после каждой смены схемы и минимум раз в неделю, check каждые 30s с
timeout <30s для штатного объёма БД; monitor может читать большой архив, поэтому
нужно измерить длительность перед установкой этого timeout. Предотвращать
overlap запусков, отслеживать не только exit 1/2, но и отсутствие успешного
запуска независимым supervisor. Обновлять --archive только после exit 0 backup.
Не помещать URL/ключ в Task Scheduler arguments. Отдельный одобренный delivery
adapter должен доставлять transition/recovery и ошибки; вывод CLI сам по себе
не является доставкой оповещения человеку. SLO <2min пока не измерен.

Local encrypted backup и sanitized operator reports — максимум 7 суток.
Проверить свежую копию и drill перед ручным удалением старых; последнюю
работоспособную копию не удалять автоматически. Никаких sweeping recursive
deletes скрипт не выполняет. Operator reports не включают исключения/дампы.
Ключ отдельно от backup, резерв ключа — локальный secret manager; его потеря
делает архив невосстановимым. При ротации сохранить старый ключ до истечения
retention соответствующих копий. Fernet timestamp виден в ciphertext.

Аварийный kill/потеря PostgreSQL может помешать finally cleanup. После такого
сбоя DBA проверяет только `vpn_i016_drill_` targets на изолированном test cluster,
не удаляя БД по одному лишь совпадению префикса: сначала установить владельца
и отсутствие выполняющегося drill. Штатные success/failure cleanup проверены.

Production/off-host storage, deletion/legal policy, защита ключа secret manager,
доставка, scheduler, физический restore инфраструктуры и отзыв credentials
после disaster restore требуют отдельного deployment/privacy gate. Эти
инструкции не являются разрешением на внешние действия.

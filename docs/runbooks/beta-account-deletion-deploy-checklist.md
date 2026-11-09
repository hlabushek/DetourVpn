# Деплой `0019_account_deletion` на beta: пошаговый чек для владельца

Статус: подготовка 2026-11-08. **Ничего из этого не выполнено и не разрешено этим файлом.**
Деплой, бэкап beta и любые действия на узлах делает владелец (AGENTS.md). Не записывайте в
receipts IP, пароли, ключи и сырые журналы, только булевы значения и хэши.

## Что именно меняется

- Миграция `services/api/migrations/versions/0019_account_deletion.py` (родитель
  `0018_peer_window_resolution`): колонка `users.deleted_at`, `ck_users_status` теперь допускает
  `deleted`, новый `ck_users_deleted_at` связывает статус и время. Данные не переписываются,
  индексов не добавляется: по `deleted_at` запросов нет, а `ix_users_created_at` остаётся.
- Новый API: `POST /api/v1/me/delete` (ADR-091), область `account:delete` только у нативной
  сессии, обновлённый `openapi.json`.
- Клиент (кнопка «Удалить аккаунт») ставится отдельной сборкой APK, см. `beta-owner-manual-gates.md`.

## Проверено локально (2026-11-08, одноразовая PostgreSQL, не beta)

- `test_migrations.py`: 13 тестов, включая новый `test_account_deletion_migration_constraints_and_rollback`:
  ограничение `deleted <-> deleted_at` в обе стороны, downgrade на `0018` при живых аккаунтах
  проходит, повторный upgrade проходит, при существовании удалённого аккаунта downgrade
  отказывает (`deleted accounts exist`) и ревизия остаётся `0019`.
- `test_account_deletion.py`: сценарии удаления, 401 при повторе, 422 при неверном подтверждении,
  изоляция чужого аккаунта, отзыв инвайта.
- Не проверено: применение на копии реальной beta-базы, нагрузка, узловая часть (ACK снятия peer).

## Порядок для владельца

Образец процедуры: `scripts/deploy_i030_unknown_resolution.ps1` (так выкатывали `0018`: проверка
хэшей, бэкап, остановка API, миграция через `scripts/closed_migrate.py` под ролью `vpn_migrator`,
откат файлов при сбое). Отдельного скрипта для `0019` в репозитории **нет**; не запускайте скрипт
для `0018` для этой цели. Создать такой скрипт по тому же образцу можно отдельным заданием.

1. **Подготовка (чтение).** Убедитесь, что локальная ветка собрана из закоммиченного состояния,
   запишите SHA-256 файлов миграции и изменённых модулей API; `uv run --frozen python scripts/export_openapi.py --check`.
2. **Базовые замеры хоста.** `df -Pk /`, `free -m`, состояние сервисов (`detourvpn-beta-api`,
   `detourvpn-beta-database`, `detourvpn-beta-node-agent` на узле), `/api/v1/ready` = 200.
   Миграция лёгкая (DDL на таблице `users` из единиц строк), но свободное место нужно для бэкапа.
3. **Бэкап до изменений.** Запустите штатный бэкап (`systemctl start detourvpn-beta-backup.service`
   или задача `DetourVpn Beta Backup Daily`), скачайте свежий `.enc`, проверьте, что он новее
   времени начала работ, и что восстановление проходит на одноразовой PostgreSQL (раздел 3
   `beta-owner-manual-gates.md`). Без этого дальше не идти.
4. **Остановка записи.** Остановите `detourvpn-beta-api.service` (клиенты получат ошибку на минуты;
   тестеров в этот момент быть не должно, бета ещё не открыта).
5. **Миграция.** Положите файл `0019_account_deletion.py` и новые модули API по образцу `0018`,
   запустите `scripts/closed_migrate.py` (в нём же отзываются лишние права `alembic_version`,
   `quota_ledger`). Ожидаемый вывод: `closed_migration_passed`.
6. **Проверка ревизии.** `SELECT version_num FROM alembic_version` = `0019_account_deletion`;
   в `users` есть `deleted_at`; число пользователей не изменилось.
7. **Запуск API.** `systemctl start detourvpn-beta-api.service`, `/api/v1/ready` = 200,
   `GET /openapi.json` содержит `/api/v1/me/delete` (если OpenAPI открыт, иначе пропустить).
8. **Свежий бэкап уже на схеме 0019.** Старые архивы (схема 0018) дают `schema_mismatch` в
   restore drill на новом коде, это ожидаемо по `check_schema`. Сразу сделайте новый бэкап и
   повторите restore drill.
9. **Откат, если что-то пошло не так до открытия тестеров.** Пока нет удалённых аккаунтов, откат
   схемы: `alembic downgrade 0018_peer_window_resolution` под `vpn_migrator`, затем вернуть файлы
   API. Если удалённый аккаунт уже существует, downgrade намеренно отказывает: восстанавливайтесь
   из бэкапа шага 3 или оставьте схему.
10. **Проверка удаления** на втором тестовом аккаунте: `beta-owner-manual-gates.md`, раздел 6.

## Что записать

`backup_fresh_before: true|false`, `restore_drill_before: pass|fail`, `revision_after: 0019`,
`ready_after: 200`, `backup_after_on_0019: true`, `restore_drill_after: pass|fail`,
`deletion_check: pass|fail|not_run`. Отдельным файлом в `docs/evidence/`.

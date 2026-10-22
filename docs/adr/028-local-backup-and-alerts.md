# ADR-028 — I-016: локальные backup, restore drill и alerts

Принято 2026-10-21. Первый эксплуатационный контур — локальный PostgreSQL.
Развёртывание и внешние уведомления требуют отдельного разрешения владельца.

- Используем существующую cryptography/Fernet, без новых библиотек и своего
  криптографического формата. Один аутентифицированный token содержит JSON v1:
  custom pg_dump, UTC timestamp, привязку к источнику и manifest всех public
  tables (число строк и SHA-256 канонических строк). Manifest и dump берутся
  из одного экспортированного REPEATABLE READ snapshot. SHA-256 здесь проверяет
  восстановление; аутентификацию обеспечивает Fernet.
- Дамп целиком в RAM: предел 32 MiB, subprocess timeout 60s. Это осознанная
  граница локального MVP, не решение для больших production баз. Plaintext
  дамп не пишется на диск. Swap/OS crash dumps вне этой гарантии.
- Ключ генерируется локально отдельно, не передаётся в аргументах/логах.
  Файлы создаются эксклюзивно, mode 0600; Windows требует заранее ограниченного
  ACL каталога. Ключ хранить отдельно от backup. Каталоги secrets/backups ignored.
- Restore drill только на literal loopback, без URL query overrides, в новую
  случайную БД из template0; никогда не восстанавливает в заданную пользователем
  существующую БД. pg_restore без owner/ACL, single transaction, exit-on-error.
  Проверяем полный manifest и existing ledger reconciliation, затем DROP только
  созданной этой операцией БД, включая failure paths. Cleanup failure — ошибка.
  Архивы принимаются только от доверенного локального источника: pg_restore
  исполняет SQL из архива. Не использовать production credentials или кластер
  с ценными данными для drill; восстановленные credentials не запускать в API.
- Локальный read-only monitor: schema head, активные nodes с last_seen старше
  90s/NULL/будущим временем, pending peer commands старше 90s, свободный диск,
  подлинность/возраст backup <=24h и привязка к базе. Disabled nodes исключены.
  Пустой registry выдаёт node_inventory_empty; не означает рабочий data plane.
  Ошибка любой проверки даёт стабильный alert code и nonzero exit.
- Monitor печатает только UTC, codes и counts; не пишет identities, IP, DNS,
  URL, payloads, SQL/exception text или credentials. Внешняя доставка не включена.
  Для staging нужен scheduler каждые 30s и независимый supervisor его работы;
  это только проектная цель detection <2min, не измеренный SLO.
- Local retention: до 7 дней для encrypted backups и sanitized operator reports,
  drill DB живёт только во время команды. Удаление старых файлов — вручную
  после проверки свежего backup/drill, без автоматического удаления последней
  копии. Production collection запрещена до общей retention/privacy политики.
- Нет миграций, API/Android/VPN изменений, удалённого хранилища, автоматического
  восстановления сервисов или внедрения scheduler в инфраструктуру.

Основания: [Fernet](https://cryptography.io/en/stable/fernet/) требует всю
запись в памяти; [pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html)
исполняет SQL и поддерживает восстановление в новую template0 database.

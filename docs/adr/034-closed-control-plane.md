# ADR-034 — постоянный control plane для закрытого бесплатного сервиса

Дата 2026-10-23. Решение для локальной реализации; не разрешение deployment.

Один Linux host: PostgreSQL 17 в Compose с persistent volume и портом только
127.0.0.1:15433. API — отдельный непривилегированный systemd process на loopback
8000. TLS завершается отдельно на Caddy. API не доверяет forwarded headers.
PostgreSQL соединение остаётся loopback, без SSH-forward и без ослабления
существующего URL validator. Внутрихостовый plaintext допустим в этой модели;
для отдельного DB host требуется новый ADR с verify-full TLS и network ACL.

`closed` требует auth, dedicated vpn_api/password, HTTPS public origin и
выключенную OpenAPI UI. HTTPS declaration сама не доказывает TLS: публичный
ingress должен пройти ручную проверку после разрешённого deployment.
API не получает superuser/migration credentials. Отдельные vpn_migrator
(владелец schema), vpn_api (DML), bootstrap cluster admin. Нет Redis: существующие
PostgreSQL rate buckets и outbox сохраняются; добавлять очередь без необходимости
не нужно. Миграции не исполняются в API startup и не используют runtime role.

GET /health остаётся liveness; GET /ready требует точного совпадения Alembic
heads, работающей writable-primary DB, а в closed — безопасной runtime роли.
Это не проверка Telegram, узлов, VPN egress, TLS, backup или готовности подписок.
Ошибки БД/credentials наружу не выводятся. SQL statement timeout 2s, connect 5s.

Файлы секретов вне checkout/OneDrive, root-only, backup key хранится отдельно.
PostgreSQL logs не содержат statements/parameters. Journal приложения хранит
только sanitized ошибки; доступа URL/DNS/payload логирования нет. Operational
retention 7 суток, применение journald policy и внешние backup — следующий gate.
Пользовательские записи/ledger не чистятся системными units.

Rollback: остановить API, сохранить encrypted backup, вернуть прежний release
только при совместимой схеме. Не делать destructive downgrade/volume down -v.
Не подключать restored DB к агентам до fencing старых commands/credentials.
Миграции существующего проекта сохраняют запрет удаления непустой истории.

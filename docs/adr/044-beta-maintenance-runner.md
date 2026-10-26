# ADR-044 — I-027 local serialized maintenance runner

Статус: принято 2026-10-25 для локальной реализации.

`scripts/beta_maintenance.py` объединяет существующую auth retention cleanup и
quota ledger reconciliation. Между процессами используется PostgreSQL
session-level advisory lock с выделенным ключом `21027001`. Он удерживается на
одном выделенном соединении от начала до конца и освобождается в `finally`;
закрытие PostgreSQL соединения также снимает session lock. Повторный запуск,
увидевший занятый lock, возвращается без cleanup с отдельным exit code.

Миграция и новая dependency не нужны. Cleanup и сверка выполняются отдельными
операциями и могут частично завершиться при сбое; повтор безопасен, так как
cleanup использует retention cutoffs, а ledger reconciliation не является
командой исправления данных. Отчёт содержит только агрегаты. Scheduler,
supervisor и alert delivery не входят в локальный runner и остаются отдельными
эксплуатационными воротами. Механизм не доказывает data-plane expiry.

Применение: один control-plane PostgreSQL. Разные изолированные БД имеют
независимые lock namespace; для разных maintenance job нужно выделять другие
ключи и документировать их владельцев.

Retention cleanup вынесен в функцию, использующую только DB engine, чтобы
maintenance process не конструировал `AuthService` и не получал Bot API token
или auth pepper. Settings читаются без dotenv fallback; systemd передаёт только
необходимый database URL.

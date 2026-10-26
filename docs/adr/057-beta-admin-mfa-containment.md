# ADR-057 — beta admin MFA containment after enrollment disclosure

Статус: beta-only containment выполнен 2026-10-26; новая MFA регистрация ожидает
локального ввода владельца.

## Контекст

Enrollment URI вновь созданного beta admin был опубликован в чате до live
использования. Неизвестные пользователи не имеют публичного admin ingress,
однако seed нужно считать раскрытым и не использовать для beta операций.

## Решение

В отдельном closed admin-only режиме с loopback origin операторский CLI может отключить только
учётную запись `owner`, отозвать все её действующие admin sessions и атомарно
записать событие в `admin_actions`. Запись identity сохраняется для ссылочной
целостности и аудита. CLI проверяет exact beta loopback origin
(`https://127.0.0.1`) и выключенные user-auth,
Mini App и invites; он не меняет Caddy, environment, Bot API token или другую
учётную запись. Новая MFA identity создаётся отдельным интерактивным bootstrap
с новым username и seed.

Операция идемпотентна и проверяется на временной PostgreSQL, включая параллельные
повторы. Не использовать этот recovery как замену регулярному MFA/key recovery
и off-host incident procedure I-027.

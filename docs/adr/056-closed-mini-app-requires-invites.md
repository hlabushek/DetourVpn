# ADR-056 — invite enforcement обязателен для Mini App в closed-среде

Статус: принято для I-030, локальная fail-closed защита.

## Контекст

В `environment=closed` включённая Mini App могла стартовать с
`VPN_BETA_INVITES_ONLY=false`. В этом режиме корректный Telegram `initData`
позволял новому Telegram-пользователю создать аккаунт без предварительного
одобрения администратора. Для beta, опубликованной через общий HTTPS origin,
это расходится с требованием ограничить доступ конкретными приглашёнными.

## Решение

Настройки API отклоняют запуск, если `environment=closed` и `enable_mini_app=true`,
но `beta_invites_only=false`. Явный invite gate по-прежнему требует
authenticated `closed`/`test` mode. `closed` deployments с Mini App выключенной,
включая admin-only bootstrap, не затрагиваются. `test` environment остаётся
гибким для локальных тестов.

## Последствия

- Ошибочная конфигурация closed Mini App отказывает при старте, а не создаёт
  неограниченную регистрацию.
- Оператор должен включить invite gate после миграции и настроить MFA-admin до
  открытия публичного Mini App/API ingress.
- Изменение применяется только при следующем rollout кода; beta host, B,
  production и опубликованные сервисы этим ADR не меняются.
- Unit/PostgreSQL tests проверяют отрицательный и разрешённый случаи; это не
  доказывает hosted MFA enrollment или Telegram OTP.

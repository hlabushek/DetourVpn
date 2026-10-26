# ADR-058 — beta Bot API token staged without enabling authentication

Статус: принято для закрытой beta среды, 2026-10-26.

## Контекст

I-026 использует существующий Bot API token непосредственно в FastAPI: backend
проверяет Telegram `initData` и отправляет OTP. Telegram Serverless endpoint и
его CLI access credential для этого не подходят и не используются. На beta API
пока выключены auth/Mini App/invites, а внешний HTTPS site отвечает 404.

## Решение

Владелец вводит token через скрытый `Read-Host -AsSecureString` в локальном
PowerShell. Скрипт отправляет его root процессу beta API host только через
зашифрованный SSH stdin, не в argv, environment, URI, log или repository. На
beta принимаются только ожидаемый `@uysdgyfudf_bot`, точный закрытый admin-only
режим, loopback origin и root-only `api.env` (0600). Запись трёх bot settings
атомарна; существующие ключи запрещают автоматическую ротацию. Token ID выводится
из синтаксического префикса token; сетевой `getMe` не вызывается.

Хранение token **не включает** auth, Mini App или invite enforcement, не меняет
Caddy/BotFather/webhook и не перезапускает API. Для использования token потребуется
отдельная reviewed beta activation и ingress acceptance. Rollback — удалить только
три добавленных `VPN_TELEGRAM_BOT_*` строки с сохранением режима `0600`.

## Ограничения

SSH stdin и процесс PowerShell временно обрабатывают plaintext в памяти; на beta
host token хранится в root-only `api.env`. Не делать снимки окна и не отправлять
token в чат. Это credential staging, не live Telegram/OTP acceptance.

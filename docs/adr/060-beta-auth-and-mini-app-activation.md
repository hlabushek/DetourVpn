# ADR-060 — включение invite-only auth и Mini App на beta API

Статус: принято и применено только к beta API, 2026-10-26.

## Решение

Включать Telegram auth и Mini App только вместе с `VPN_BETA_INVITES_ONLY=true`.
Одновременно отключать `VPN_CLOSED_ADMIN_ONLY`, сохраняя отдельную MFA admin
identity. Перед записью проверять staged Bot API token без его вывода, таблицу
приглашений, ожидаемое состояние двух admin identity и неизменённый закрытый
режим. Выпускать static Mini App в новой неизменяемой папке releases; settings
файл менять атомарно и сохранить root-only backup. После рестарта проверять API
readiness и Mini App локально; при неуспехе восстанавливать env из backup.

Публичный Caddy устанавливать отдельным шагом: точный allowlist из ADR-059,
target-version validation, резервная копия и reload с rollback. Beta ingress
не является приглашением пользователей и не меняет A/B или production.

## Причины и ограничения

Так Mini App может быть публично загружена, но новые учётные записи/сессии
проверяются закрытым invite gate. Публичные URL сами по себе не подтверждают
проверку Telegram `initData`, OTP доставку, вход в Android или VPN приватность.
Node ingress, Telegram Serverless deployment, конкретные invites и I-026/I-027/
I-028 acceptance остаются отдельными воротами.

## Реализация и rollback

`scripts/beta_activate_auth.py` делает fail-closed preflight, атомарную запись,
и поддерживает rollback только из root-owned `api.env.pre-auth-<32 hex>` файла
с mode 0600 и закрытыми исходными flags. `scripts/start_beta_auth_activation.ps1`
собирает отдельный Mini App release и меняет только beta API. Caddy
`Caddyfile` имеет отдельную backup-копию; восстановить её следует вместе с
`systemctl reload caddy`. Резервные файлы и release artifacts не удалять без
проверки текущих ссылок/использования.

Внешняя проверка 2026-10-26: trusted TLS Mini App и JS отвечают 200;
readiness/internal/webhook/unknown/root отвечают 404; allowed OTP route
отвечает 405 на GET, invalid empty POST даёт 422 до Telegram/API side effect.
Реальный initData, OTP и BotFather/Serverless ещё не проверялись.

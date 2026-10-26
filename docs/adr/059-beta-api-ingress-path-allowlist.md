# ADR-059 — beta HTTPS ingress path allowlist

Статус: принято и активно на beta Caddy с 2026-10-26.

## Контекст

`infra/beta/Caddyfile.ip` является будущим шаблоном API ingress; активный
`Caddyfile.ip-closed` на beta host возвращает HTTPS 404. В шаблоне ранее весь
остаток HTTPS traffic проксировался в FastAPI после отдельного source-IP matcher
для трёх v3 node endpoints. Это выставляло наружу любой новый FastAPI route,
включая не предназначенный для тестеров.

## Решение

Будущий template проксирует только `/mini-app`, `/admin`, `/api/admin/v1/*` и
allowlist пользовательского API, который использует Android/Mini App: auth,
devices, nodes, quota, tunnels, window-tunnels, plans/subscriptions, rewards и
referrals. Отдельный matcher по-прежнему пропускает только три POST v3 window
routes с прямого beta node IPv4. `/api/internal/*`, `/api/v1/ready`, Telegram
webhook, docs/OpenAPI и все неизвестные пути завершаются 404.

Приложение сохраняет свою authz: login/OTP endpoints требуют свежий Telegram
`initData`/challenge и rate limits; остальные user routes требуют bearer session;
admin API использует password+TOTP; node routes используют source-IP matcher и
подписанный node credential. Caddy allowlist не заменяет эти проверки.

## Проверки и границы

Локальный config test проверяет перечисленные клиентские префиксы, порядок deny
rules и закрытый fallback. Target Caddy 2.11.7 adapter подтвердил, что повторные
`path` directives в `@beta_api` объединяются в один список разрешённых путей.
Первый activation rollout обнаружил пропущенный exact path `/api/v1/referrals`:
Mini App использует его напрямую, а один wildcard `/api/v1/referrals/*` не
совпадал с корнем. Тест расширен; exact path добавлен рядом с wildcard. Новый
шаблон повторно прошёл target Caddy 2.11.7 validation, установлен на beta host
с backup `Caddyfile.pre-beta-auth-3d51fd1d900e4b6fb0d572285d9cd8c3` и успешно
reload. Внешний GET `/api/v1/referrals` теперь отвечает API `401` без сессии,
а не ingress `404`. Unknown/internal/readiness по-прежнему 404. Это исправляет
route gate, но не заменяет Telegram auth/OTP acceptance.

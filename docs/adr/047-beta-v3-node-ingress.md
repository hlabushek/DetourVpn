# ADR-047 — ограниченный HTTPS ingress для beta v3 node

Статус: принято для локального шаблона I-030; Caddy/firewall/node hosted acceptance
не выполнена.

## Контекст

`services/node-agent/window_daemon.py` уже инициирует outbound HTTPS к control
plane и ограничивает операции тремя v3 маршрутами: `inventory`, `start`, `poll`.
API независимо проверяет node ID, scoped credential, timestamp и одноразовый
nonce. Сам API-router принимает только POST. Текущий закрытый Caddy template
скрывал все `/api/internal/*`, поэтому отдельному beta node некуда было безопасно
poll-ить без временного staging relay. Вебхуки, arbitrary paths и shell relay для
этого не нужны.

## Решение

Beta API получает прямые HTTPS-запросы от одного beta node со статическим source
IP. В `infra/beta/Caddyfile.ip` только точные POST пути
`/api/internal/v3/windows/{inventory,start,poll}` проходят, только если IP
непосредственного сетевого peer равен `DETOURVPN_BETA_NODE_IP`. Остальные
`/api/internal/*` и `/api/v1/ready` отвечают 404; обычные публичные API paths
продолжают проксироваться на loopback API.

Фильтр Caddy дополняет, но не заменяет аутентификацию API и host firewall. API
по-прежнему проверяет credential и anti-replay поля. Firewall должен разрешать
входящий HTTPS на control-plane host по его публичной beta policy и не должен
создавать отдельный открытый node port; если политика публичного API позволяет,
узловые источники ограничиваются тем же адресом. До hosted проверки matcher
должен видеть node как прямого peer: не ставить перед Caddy неучтённый proxy или
CDN. Переход за proxy потребует заново проверить источник IP и trust headers.

`DETOURVPN_BETA_NODE_IP` обязателен и должен быть конкретным адресом узла, не
широким CIDR. Без него конфигурация не должна приниматься при `caddy validate`.
Оператор обязан синхронно обновить Caddy и firewall после смены адреса. До этого
изменения соединение node→API не проходит. Шаблон не включает source IP и не
привязывает route к A/B.

`handle` блоки взаимоисключающие: узкий разрешённый matcher предшествует
внутреннему deny, а публичный reverse proxy остаётся fallback. Правило опирается
на официальные Caddy `remote_ip` exact-IP matching и `handle` first-match
семантику:
[remote_ip matcher](https://caddyserver.com/docs/caddyfile/matchers),
[handle directive](https://caddyserver.com/docs/caddyfile/directives/handle).

## Не входит в это решение

- Установка/обслуживание VPN node, AWG binaries, namespace/slot/core/systemd и
  host firewall.
- Provisioning node credential, inventory и v3 manifest; Android или physical
  VPN acceptance.
- TLS IP certificate provisioning, Caddy/firewall deployment, внешний монитор
  или разрешение на любую инфраструктуру.

## Проверки и риски

Локальные статические тесты проверяют точное множество путей, HTTP method,
положение allow/deny/fallback блоков и отсутствие proxy в bootstrap Caddyfile.
Оба Caddyfile также прошли `caddy validate` под Caddy 2.11.4 в Kali WSL с
TEST ONLY IP placeholders и временным self-signed TLS certificate. Проверялся
локальный установленный пакет, не целевая версия beta host. Перед hosted use
нужно повторить validation на точной target version, затем проверить разрешённый
и посторонний source IP, а также API node credential/nonce/replay в реальном
зашифрованном transport до маршрутизации трафика друзей.

Главные риски: ошибка matcher-порядка может открыть internal API; ошибочный
proxy перед Caddy меняет смысл `remote_ip`; изменившийся node IP вызывает отказ
control transport. Hosted использование запрещено до проверки каждого из них.

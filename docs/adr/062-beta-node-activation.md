# ADR-062 — guarded activation отдельной beta VPN-ноды

Статус: принято; активация beta control plane выполнена 2026-10-26.

## Контекст

I-030 подготовила отдельные beta API и VPN node: provisioning выдавал disabled
запись, credential и manifest, но безопасный startup/rollback gate и live
agent-auth polling ещё не были проверены. Требовалось включить только beta runtime,
не меняя B/production и не открывая друзей до проверки пользовательского туннеля.

## Решение

Добавить `scripts/activate_beta_node.py` как ограниченный операторский runner с
фиксированными beta API/DB/node targets. Перед изменением он проверяет закрытый
beta deployment, manifest, ожидаемые сервисы и node files. Сначала сохраняет
root-only backup API env, включает `VPN_ENABLE_WINDOW_TUNNELS` и manifest path,
перезапускает beta API и проверяет readiness. Затем переводит только закреплённую
node row из disabled в active, запускает node-agent и ждёт runtime identity и
свежий `last_seen`. Runner отказывает при неожиданных peer sessions или несовпадении
целей.

Автоматический и ручной rollback останавливают node-agent, проверяют отсутствие
активных peer connections, переводят beta node в disabled, восстанавливают API env
из root-only backup и перезапускают только beta API. Backup tag хранится в receipt;
секретные значения в receipt не включаются. Ручной rollback запускается через
локальный runner по инструкции в `../runbooks/beta-environment.md`.

## Проверки и границы

16 activation tests и 40 объединённых provisioning/binding/activation tests
прошли. Ruff, format check и mypy прошли. Дополнительно 46 API tests прошли с
временным локальным PostgreSQL 17; beta DB не использовалась. На beta проверены
readiness 200, закрытый invite-only режим, активные API/node-agent/bootstrap/core
services, свежий runtime и аутентифицированный polling. Активных peer connections
на последней проверке было 0.

Активация не доказывает внешний UDP ingress, peer handshake, Android VPN tunnel,
DNS/IPv6 privacy, двухустройную работу или recovery после provider outage. Не
приглашать друзей до закрытия I-026/I-027/I-028 и проверочного peer/tunnel smoke.
Не заявлять Always-on/Lockdown или kill switch. B/production, DNS, платежи и
публикации не затрагивались.

Receipt: `../evidence/i-030-beta-node-activation-20261026.json`.

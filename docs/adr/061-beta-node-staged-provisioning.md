# ADR-061 — staged provisioning credential для beta node

Статус: принято; staged provisioning и binding выполнены. Позднее beta node
активирована по отдельному решению ADR-062. Исторические формулировки ниже
описывают состояние до активации; текущее состояние и ограничения — в
`../evidence/i-030-beta-node-activation-20261026.json`.

## Контекст

Node agent принимает bearer credential только для закреплённого node UUID. Если
узел активировать до проверки private runtime files и API window manifest,
ошибка конфигурации может открыть недоступный либо неверно привязанный узел.
Существующий `NodeRegistry.provision()` сразу создавал активную запись и один раз
возвращал credential вызывающему коду.

## Решение

Сохранить прежнее поведение по умолчанию для действующих вызовов, добавив явный
`initial_status="disabled"` для staged provisioning. Локальный beta-only CLI
читает только root-private `/etc/detourvpn-beta/api.env`, игнорирует унаследованные
`VPN_*` overrides и проверяет закрытое окружение, user auth, Mini App, invite-only,
роль `vpn_api`, базу `detourvpn_beta` и loopback-порт `25433`. Он создаёт
узел только с именем `detourvpn-beta-helsinki`, затем эксклюзивно сохраняет
credential в `/run/detourvpn-beta/node-credential-<idempotency-key>.json` с
root-only правами. CLI выводит UUID и путь, но не credential. Он не добавляет
API route, не включает window tunnels и не активирует узел.

Повтор по существующему idempotency key не выдаёт credential повторно. Если БД
успела создать disabled row, а runtime-файл не записан, запись остаётся
неактивной и требует операторского восстановления; повторный вызов не может
восстановить исходный секрет.

## Проверки и ограничения

Synthetic provisioning/binding tests: **23 passed**; Ruff, format check, mypy и
syntax compile всех встроенных remote programs прошли. Четыре PostgreSQL
`test_nodes.py` теста также прошли на disposable PostgreSQL 18 в Kali WSL; кластер
удалён автоматически, beta DB не использовалась.

На beta API host подтверждены readiness 200, DB target
`detourvpn_beta/vpn_api/127.0.0.1:25433`, disabled node row и root-owned manifest
0640. На beta node проверены root-owned `agent.json` и credential 0600; node-agent
остаётся `inactive`. Одноразовый credential source в `/run/detourvpn-beta/`
удалён после проверки обоих hosts. Window tunnels выключены, manifest path не
подключён к API settings.

Четыре архивные payload-копии на beta node были root-owned и не имели ссылок из
systemd/cron, открытых fd или mount points. Содержимое сохранено, права ограничены
до каталогов 0700 и файлов 0600. Bootstrap/core units остались active, node-agent
inactive. Node-agent authenticated inventory/start/poll и replay, внешний peer
handshake и Android privacy acceptance остаются открытыми. Перед активацией нужен
отдельный review startup/rollback. См.
`../evidence/i-030-beta-node-binding-20261026.json`.

Позднее startup/rollback review был реализован в `scripts/activate_beta_node.py`;
активация выполнена отдельно по ADR-062. Текущий node-agent и API window-tunnel
control plane активны, но peer connection не было. Эта запись не означает
прохождение provider UDP ingress, Android tunnel или privacy acceptance.

# ADR-048 — systemd supervision для beta v3 node agent

Статус: принято для локального service template I-030; не установлено и не
принято на Linux host.

## Контекст

`services/node-agent/window_daemon.py` умеет безопасно закрывать все известные
слоты, поднимать новый runtime epoch и затем общаться с API. Он требует уже
подготовленные root-private `/run/detourvpn-node/slots/*.json`, фиксированный
AWG runtime и credential из `/etc/detourvpn-node/credential`. Он не создаёт
network namespaces, AWG cores, маршруты, NAT или firewall. Временный
`infra/staging-v3/runtime.py` привязан к staging A и ограниченному окну; его
нельзя использовать как beta supervisor.

## Решение

Для отдельного beta node добавить systemd unit с фиксированным executable,
root-only конфигурацией по существующему daemon contract, `UMask=0077`, закрытой
файловой системой и доступом на запись только к volatile runtime и persistent
daemon journal. Unit требует root, потому что дочерним fixed `nsenter`, `nft` и
AWG операциям нужны привилегии текущей реализации. Network families включают
AF_NETLINK для kernel control и IPv4/IPv6/Unix sockets для agent/API и локальных
инструментов.

Запуск условно допускается после появления credential, `agent.json` и
постоянного `slots.json`. `ExecStartPre` проверяет readiness каждого настроенного
слота и совпадение его public key с persistent slots config. Сам unit и preflight
не создают эти файлы, core или сетевые ресурсы. После ошибки systemd
перезапускает daemon с короткой паузой. Ранний останов daemon закрывает известные
ему gates по коду, однако unit не является supervisor для native AWG cores и не
гарантирует их восстановление после reboot.

## Проверки и ограничения

Локальные тесты проверяют статические systemd directives и фиксированные пути;
key validator покрыт synthetic тестами. Полный root-owned filesystem preflight и
его запуск на целевом Linux пока не принят. Нужны `systemd-analyze verify` на
целевом Linux/systemd, проверка владельцев/режимов файлов, отдельная проверка
слотов и firewall, затем restart/reboot и API-outage drills по I-027/I-028.
Пока нет root-owned beta slot bootstrap, AWG core services, firewall policy и
hosted acceptance, этот unit нельзя включать и считать persistent beta node.

# ADR-017: изоляция тестового peer на занятом staging host

Status: **Bounded runtime acceptance verified; public ingress design remains Proposed**, 2026-10-19.
Scope: I-010 only; no production deployment or I-012 implementation.

## Основание

На одобренном staging `45.10.166.106` read-only проверка обнаружила Docker,
процесс с именем `amneziawg-go`, bridge `amn0`, несколько veth и UDP listeners
34053/34610. IPv4 forwarding уже равен 1, IPv6 forwarding — 0. Каталоги нашего
релиза принадлежат root и имеют права 0755. Полный вывод live preflight от владельца подтвердил
`amnezia-awg2` → UDP 34053 и `amnezia-wireguard` → UDP 34610 через Docker proxy
на IPv4/IPv6 wildcard addresses; `ok=3 changed=0 failed=0`.
Host main routes занимают `172.17.0.0/16` и `172.29.172.0/24`. Расширенный live preflight владельца
(`ok=5 changed=0 failed=0`) подтвердил внутреннюю `10.8.1.0/24` в обоих
контейнерах: `awg0` у AWG и `wg0` у WireGuard. Оба подключены к `bridge` и
`amnezia-dns-net`; одинаковые подсети в раздельных namespaces сами по себе
не означают конфликт. Исправность существующих VPN и пользователи не проверены.

Владелец попросил сначала выяснить назначение существующего VPN. Предположение
до выяснения: сервер занят, существующие подключения необходимо сохранить.
Ранее подготовленный playbook размещает только файлы; он не создаёт эти сети.

## Решение для следующего шага

Host metadata inventory выполнен владельцем через `staging-network-preflight.yml` с интерактивным sudo через
`vpn-deploy`. Он разрешён только для одного staging SSH target, сверяет имя
inventory и явно переданный адрес. Вывод ограничен UDP socket owners, main
routes IPv4/IPv6, названиями/образами/портами контейнеров и именами сетей Docker.
Нет `docker exec`, полного inspect, чтения конфигов, env, ключей или захвата
пакетов. Ошибка любой команды останавливает процедуру; `changed=0` обозначает
чтение, а не готовность узла. Команды чтения выполняются и с `--check`.

Диагностика расширена для следующего запуска: `files/staging_network_inventory.py`
читает IPAM всех перечисленных Docker networks и attachments только двух
фиксированных контейнеров. Через host `nsenter --net=/proc/<PID>/ns/net --`
выполняется исключительно host `/usr/sbin/ip`: JSON routes всех таблиц и rules
IPv4/IPv6. Mount/user/PID namespaces не переключаются; исполняемые файлы
контейнеров не запускаются. Нет shell, `docker exec`, volumes, env или configs.
PID должен быть целым >1, container ID — 64 hex символа, контейнер — running.
ID/PID/start time/attachments сравниваются до и после чтения; при изменении
снимок отклоняется. Это контроль частых гонок, а не атомарный snapshot всей сети.
Динамические изменения маршрутов и policy routes клиента остаются ограничением.

Helper выводит один JSON только после успешного завершения всех probes;
неизвестные поля отбрасываются, raw stdout/stderr при ошибке не печатаются.
Ansible скрывает task с кодом helper через `no_log`, а отдельный debug показывает
только успешно собранный выбранный JSON. Ошибка task означает неполный inventory:
не использовать ранее напечатанные пять блоков для выбора подсети. Новые Python
зависимости не добавлены; нужны уже имеющиеся host Python, Docker, ip и nsenter.
Live privileged запуск расширения выполнен владельцем; проверен полный вывод.
Минимальная сводка без ключей и endpoint-идентификаторов:
[receipt](../evidence/i-010-network-summary.json).

Источники синтаксиса:
[Ansible command argv](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/command_module.html),
[Docker container ls format](https://docs.docker.com/reference/cli/docker/container/ls/),
[Docker inspect format](https://docs.docker.com/reference/cli/docker/container/inspect/),
[nsenter network namespace](https://man7.org/linux/man-pages/man1/nsenter.1.html).

## План тестового туннеля после inventory

1. Назначение опубликованных портов установлено; сохранить оба контейнера.
   Внутренние routes/network attachments получены без чтения ключей и конфигов. Существующие
   контейнеры, сети, конфиги и peer не переиспользовать и не перезапускать.
2. Выбрать отдельные имя интерфейса, свободный UDP-порт и непересекающуюся
   тестовую подсеть только после сравнения host/Docker routes и клиентской сети.
   До этого конкретные значения не назначать. Network namespace — кандидат на
   изоляцию, а не уже проверенная реализация. Если безопасное сосуществование
   не подтверждено, использовать другой явно одобренный staging host.
3. Сначала проверить ограниченный peer-to-peer маршрут без default route и NAT.
   Private node key генерируется на узле штатным `awg genkey`, хранится вне
   репозитория с правами 0600 в каталоге 0700. Private client key генерируется
   локально на тестовом клиенте; backend его не получает. Ключи, полный config,
   `awg showconf`/dump и packet captures не выводятся в чат, journal и отчёты.
   Конкретные AWG параметры требуют проверки pinned UAPI; не переносить
   параметры из уже существующего личного VPN.
4. Предложить отдельный service unit с необходимыми правами на TUN/network
   namespace, ограниченными путями записи и без payload/access logging.
   Проверить фактическую совместимость ограничений с pinned binary до запуска.
   Не давать root произвольным аргументам от клиента/control plane.
5. Перед изменениями зафиксировать маршруты, правила и работоспособность
   существующего VPN. Подготовить rollback, удаляющий только созданные этим
   тестом интерфейсы/правила/unit; не делать flush firewall, рестарт Docker,
   смену SSH или глобальный сброс forwarding. Таймер аварийного отката должен
   быть установлен и проверен до изменения сети; при невозможности — не менять.
6. Только после review отдельного diff переходить к NAT/egress. Для первого
   Android full-tunnel теста требуется явная DNS/IPv6 policy: IPv4-only маршруты
   без контроля IPv6 нельзя считать защитой от утечки. До I-011 не выдавать
   пользовательскую конфигурацию и не отмечать connect/egress/revoke как pass.

## Критерии проверки и ограничения

До применения: syntax check, отказы на неверном approval/address/user/числе
hosts, реальный metadata inventory. До открытия порта: source/build security
gates ADR-003, перечень точных сетевых изменений и проверенный rollback.
После применения: повторяемость, handshake без вывода ключей, ограниченный
маршрут, прежний VPN/SSH остаются доступны, удаление тестового peer реально
прекращает тестовый доступ. Android egress остаётся отдельным I-011 evidence.

Этот ADR не утверждает существующий VPN как часть нашего продукта. Квота,
lease enforcement и node agent не реализуются этим шагом. Live metadata
diagnostic требует локального ввода sudo-пароля; нового разрешения на уже
одобренный staging для такого чтения не требуется.

## Локальный runtime spike до изменения сети VPS

`scripts/awg_local_smoke.py` по умолчанию работает только под root в локальном
WSL. Для заключительного I-010 acceptance добавлен явный approved-staging flag
с Ubuntu/IP checks; ограниченная VPS-процедура описана в [ADR-018](018-staging-node-acceptance.md). Копирует public pinned binaries в отдельный private executable `/tmp` directory,
проверяет оба SHA-256 и возможность исполнения до запуска. Keys/config остаются
в private `/run` tmpfs, включая host с noexec (исправление первого VPS run). Два свежих namespace соединяются veth без
default route, NAT, forwarding и связи с внешними интерфейсами. Фиксированные
документационные подсети `192.0.2.0/30` (underlay) и `198.51.100.0/30` (tunnel)
существуют только внутри лаборатории. UDP 51831 не открывается на host/VPS.

Штатный `awg genkey` создаёт runtime keys и shared header protection key;
применены AWG-specific J/S/H и header protection параметры из pinned upstream.
Это тестовый profile, не production recommendation и не доказательство DPI
совместимости. Keys и configs не печатаются; config создаётся 0600 в 0700
каталоге `/run` tmpfs и удаляется сразу после setconf. Silent Go logging и
подавление stdout/stderr не позволяют передать секреты через ошибку команды.

Два фактических положительных запуска подтвердили handshake, двусторонний ping,
ненулевые rx/tx и прекращение ping после удаления server-side peer. Это вызов
штатного tools, не проверка API revoke/leases. Ошибки после network setup и при
существующем файле ключа подтвердили очистку. Изменённый binary был отвергнут до
создания сети. Повторная проверка не нашла test namespaces, root links, sockets
или tmpfs directories. Root IPv4 routes совпали до/после каждого запуска.
Гарантия очистки не распространяется на SIGKILL, сбой ядра или отключение WSL.

Кандидат для будущего staging Linux peer — `10.203.71.0/30`: пересечения с
полученными server/container routes/IPAM и текущими Windows non-default routes
не найдены. **Не назначен и не применён**; client Android routes, свежий port
check, firewall plan/rollback и security gates ADR-003 ещё необходимы.

Финальный bounded staging acceptance по ADR-018 прошёл; I-010 завершён,
[live receipt](../evidence/i-010-staging-acceptance.json) проверен. Предлагаемые
выше сетевые настройки внешнего peer пока не применены; их readiness не следует
из успешного namespace test.

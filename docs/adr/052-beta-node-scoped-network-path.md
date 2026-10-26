# ADR-052 — scoped network path для beta AWG slots

Статус: локальный код и TEST ONLY packet-path acceptance в изолированной WSL
среде; target-host/deployment acceptance не выполнена.

## Контекст

I-030 уже поднимал AWG core в отдельном network namespace с закрытым nft gate,
но namespace не имел внешней сети. Staging A содержит жёстко заданные адреса и
меняет host `net.ipv4.ip_forward`; эти runtime actions нельзя переносить на
постоянную beta-ноду без отдельной сетевой политики.

## Решение

- Использовать закреплённые системные `iproute2` и `nftables`; новых библиотек и
  криптографических компонентов не добавлять.
- Для каждого слота создать `dvbhN`/`outer0` veth и отдельный `/30` из
  `10.203.82.0/26`. До изменений проверить конфликт этого пула с host routes и
  адресами, фиксированный endpoint, единственный выбранный uplink, занятые AWG
  ports и отсутствие одноимённых nft tables/interfaces. При неоднозначности
  отказать.
- Публичный IPv4 endpoint должен быть напрямую назначен выбранному uplink и
  совпадать с source address маршрута к `1.1.1.1`. Текущая реализация намеренно
  не поддерживает provider NAT, multihoming или асимметричную маршрутизацию.
- Bootstrap требует, чтобы host IPv4 forwarding был заранее включён владельцем
  выделенной ноды. Он не пишет в global `/proc/sys/net/ipv4/ip_forward` и не
  изменяет global IPv6 settings. Для persistent host это значение надо
  отдельно настроить/проверить до старта unit.
- Установить отдельные host nft tables без `flush ruleset`: deny beta-veth
  traffic к host input и любые направления/пакеты, кроме scoped outbound,
  точного UDP slot ingress через DNAT и established return. Host SNAT применяется
  только к исходному veth `/32`; namespace SNAT скрывает `10.203.71.2` за
  `outer0`.
- В slot namespace оставить default-deny input/forward, принимать только UDP
  slot port на `outer0`, пропускать peer egress только с `10.203.71.2` к
  глобальным IPv4 destinations, блокировать reserved/private/link-local ranges
  и собственный node endpoint. Включить IPv4 forwarding только внутри этого
  namespace; отключить IPv6 там же. AWG `detour_window_v3` остаётся отдельным
  ранним gate и продолжает контролировать временное открытие окна.
- Устанавливать host nft tables до появления veth. При штатном stop сначала
  закрыть gate и остановить core, удалить slot namespace/veth и только затем
  удалить свои host tables. Если namespace cleanup не подтверждён, сохранить
  host tables закрытыми. Bootstrap не удаляет таблицы при stop без readiness.

## Проверки и результат

TEST ONLY unit tests проверяют schema, адресный пул, host/namespace правила,
порядок установки и fail-closed forwarding preflight. `scripts/check_beta_slot_bootstrap.py`
выполнен под `unshare --mount --net` в Kali WSL: pinned AWG core и `awg`, три
изолированных тестовых namespace, синтетический uplink, реальный handshake,
HTTP request через туннель, host DNAT, namespace SNAT, host SNAT, возврат через
оба NAT и закрытие доступа к private host IP. Gate quota получил ненулевой
счётчик; IPv6 выключен в slot namespace. После teardown маршруты и nft ruleset
внешнего test namespace совпали с baseline; созданные veth и slot namespace
удалены. Полный прогон прошёл два раза подряд. Перед этим один 5-секундный
handshake poll timeout показал, что старт тестового клиента иногда медленнее;
после увеличения только TEST ONLY ожидания до 12 секунд оба прогона прошли.
TEST ONLY keys/config жили в private `/run` tmpfs.

Тестовый uplink существует только внутри вложенных disposable namespaces и не
имеет физического выхода. systemd start/stop были TEST ONLY controller;
systemd PID-1, Linux target host, reboot, внешний provider firewall, реальный
endpoint ingress, Android DNS/IPv6 privacy и пользовательский трафик не
проверялись. Это доказывает локальное поведение сетевого bootstrap, но не
готовность beta node.

## Риски и эксплуатационные ограничения

Host base nft chains сосуществуют с другими base chains: более ранний drop может
заблокировать beta, а внешний firewall/provider может не пропустить UDP. Это
должно быть проверено на отдельном выделенном host после отдельного разрешения.
Ограниченный адресный пул может конфликтовать с сетью оператора; preflight
откажет и не переназначит его сам. Прямой public IPv4 и единственный route source
ограничивают поддерживаемые host topology. Нет обещания Android kill switch:
I-029 остаётся открытым.

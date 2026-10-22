# ADR-015: ограниченное развёртывание одного staging узла

Status: **Implemented and verified — artifacts and bounded I-010 node acceptance**, 2026-10-19.

Фактические результаты размещения и повторного прогона:
[verification-i-010](../verification-i-010.md). Заключительный bounded runtime acceptance подтверждён ADR-018;
публичная сетевая конфигурация этим не утверждается.

## Решение

Первый test host — один явно одобренный Ubuntu 24.04 amd64 VPS с systemd и TUN.
Доступ — не root SSH user с ограниченным sudo; никаких production credentials.
На хост копируются заранее собранные и проверенные `amneziawg-go` и `awg` из
точных ревизий ADR-003, вместе с upstream license files. На VPS сборки и скачивания
из GitHub/Go proxy не будет. Нельзя подменять AWG-кандидат WireGuard reference
без нового решения.

Playbook требует явную привязку inventory name и approval marker к одному host,
проверяет локальные SHA-256 до remote setup и повторяет проверку после копирования.
Релизы лежат в root-owned каталогах. Переключение симлинка происходит только после
сверки; прежняя версия сохраняется для отката. Никаких туннелей, конфигураций,
firewall/NAT, открытых портов и secret delivery в этом шаге.
Фактический test node и DoD I-010 будут подтверждены только после согласования
стенда, сборки бинарников, запуска, повторного прогона playbook и проверки сети.

## Обоснование и границы

Upstream Go core документирует `amneziawg-go -f <interface>`, а tools — отдельный
`awg`. Pinned server binary собран и размещён на staging, но исполнение туннеля
и совместимость с Android не испытаны.
Код I-009 принимает authenticated usage samples, но нет исполняемого node agent,
peer provisioning/revoke и credential rotation. Установка бинарников не означает
рабочий control plane/data plane и не доказывает ограничение квоты.

Не выбирать ключи, адреса подсети, MTU, UDP порт, DNS и NAT до знания approved
стенда и проверки сетевого плана. Private node key должен создаваться на стенде
и не попадать в Ansible output, repo или чат. Возможность hardware isolation
ключа пока не заявляется. Перед включением сервиса отдельно проверить права,
capabilities, конфигурацию, логи и rollback.

## Перед live deployment

1. Подтвердить конкретный staging hostname/IP, владельца, дистрибутив,
   архитектуру, входящего SSH user и его scoped sudo. Проверить, что хост пустой
   или известны существующие сетевые сервисы; не останавливать чужой VPN.
2. На отдельном build host воспроизводимо собрать exact AWG source pair из ADR-003,
   `GOTOOLCHAIN=local`, проверить source/artefact hashes, licence inventory и
   применимые advisories. Задокументировать toolchain и dependency graph.
3. Проверить `--syntax-check`, предзапуск, установку, повторный прогон без изменений,
   хеши на VPS и отсутствие запущенного туннеля/порта.
4. Для действующего tunnel нужно отдельное решение по безопасному key/config
   lifecycle, least privilege, firewall/route policy и фактические I-011 tests.

Rollback: повторно выбрать прежний проверенный релиз; не удалять прошлую директорию
до подтверждённой проверки. Playbook не меняет DB и не создаёт пользователя, поэтому
миграций данных нет. Он не готов к production и не заменяет legal/security gates.

## Заключительная приёмка I-010 (2026-10-19)

[ADR-018](018-staging-node-acceptance.md) определяет bounded acceptance для
канонического «Repeatable provisioning»: repeated install и реальный протокол
на самом VPS в двух изолированных namespaces, с автоматическим rollback.
Владелец выполнил `infra/ansible/staging-acceptance.yml`; новый live receipt
проверен, staging failed=0. I-010 завершён в границе ADR-018.
Внешний ingress и физический Android/egress остаются I-011; этот acceptance
не меняет исходное ограничение artifact playbook по firewall/NAT.

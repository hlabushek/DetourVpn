# ADR-020: окно внешней приёмки I-011

Status: Accepted for bounded staging execution by explicit owner reply, 2026-10-19.
Live snapshot v2 preflight, forced-stop and actual 600s deadline passed with all
seven baseline checks restored. Physical Android handshake/egress confirmed;
individual peer revoke and node-loss disconnect recovery now passed. Technical
POC accepted within ADR-022; see verification-i-011 for limits and history.

## Решение и допущения

Продолжать I-011, не начинать I-012 до фактической сетевой приёмки. Подготовлены
`scripts/android_staging_window.py` и `infra/ansible/files/project-vpn-i011.service`.
Владелец явно одобрил WAN endpoint/NAT на staging 45.10.166.106 ответом «делай»
на предложение UDP 51892 и окна до 10 минут. Повторное согласование не требуется.
Установка/forced-stop/deadline оформлены отдельным guarded playbook
`infra/ansible/staging-android-window.yml`; последующий Android test остаётся
в границе этого разрешения. Доступ non-root SSH подтверждён, sudo требует
локального ввода владельцем; пароль не запрашивается в чате и не сохраняется.

Фиксированная граница: UDP 51892 на ens3, namespace vpn-i011, veth v11host/v11ns,
uplink 10.203.72.0/30, tunnel 10.203.71.0/30, один публичный ключ телефона.
IPv4 default route, MTU 1280, DNS 1.1.1.1 как временный resolver теста.
Использование этого resolver не означает принятой production DNS policy.
Телефонный private key не импортируется и остаётся в памяти Android POC.
AWG параметры берутся из уже проверенного I-010 smoke, HeaderProtectionKey
создаётся заново upstream tools; это секрет, профиль нельзя печатать или публиковать.

Root namespace не меняет default route/sysctl и не открывает INPUT. Ровно
отмеченные правила DNAT/MASQUERADE/FORWARD обслуживают отдельный veth. Namespace
имеет DROP INPUT/FORWARD и разрешает входящий AWG UDP и ответы на исходящий
IPv4 трафик одного peer. Private/special ranges и адрес staging блокируются
до разрешения исходящего трафика, повторная проверка есть на host. Это ограничение
тестового peer; оно не является общей защитой от атак/DDoS.

## Отказ и очистка

До первой сетевой операции: проверка root-owned скриптов/артефактов, SHA-256,
адреса/OS, tmpfs, свободных имён и порта, существующих nft/iptables правил,
host/container routes и Docker IPAM. При неизвестном состоянии — отказ.
Два существующих Amnezia контейнера должны работать; их состояние сверяется
хешами до/после. Не восстанавливать чужой firewall из старого snapshot.

RuntimeMaxSec=600 относится ко всему процессу включая setup. KillSignal=SIGKILL
и KillMode=control-group завершают runner/core; ExecStopPost запускает отдельную
очистку и при ошибке запуска, и при deadline. TimeoutStopSec=180 отведён очистке,
а не дополнительному VPN-окну. Unit не имеет autostart/restart. Это механизм,
подлежащий проверке на стенде, а не уже измеренная гарантия cutoff.

До изменения сети сохраняется baseline marker; без marker cleanup не удаляет
ресурсы. Удаляются только точные tagged rules/имена. Сначала убирается DNAT;
отдельный сбой не отменяет остальные попытки очистки. Namespace с живыми PID
не удаляется: требуется ручной разбор. При drift/error marker остаётся в /run
для повторной очистки после остановки unit. Никаких flush/iptables-restore.

Keys/configs — root-only 0600 в 0700 tmpfs directory, raw stdout/stderr утилит
не публикуются, core logging отключён, core dumps запрещены. tmpfs не гарантирует
отсутствия swap; Python/Go не обещают zeroization. Перенос профиля на телефон
требует отдельного приватного канала и удаления копий после теста.

## Приёмка

Synthetic OS/command tests и локальный systemd-analyze не доказывают работу NAT,
реальный timer cleanup, DNS/IPv6 isolation или egress. Перед Android сценарием
на стенде проверить forced stop/partial failure cleanup и восстановление snapshot.
После этого нужен новый полный десятиминутный прогон: handshake, egress, DNS/IPv6,
смена сети, disconnect и peer revoke с проверкой фактического прекращения трафика.
Пустой список peers сам по себе не закрывает revoke DoD.

Документация lifecycle: [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).
Текущий результат и ручная процедура: [runbook](../runbooks/android-poc.md).

## Уточнение rollback после первого forced-stop

Snapshot v2 исключает только динамические chain counters и route expires;
firewall policy/rules, gateway, metrics и состав маршрутов продолжают сверяться.
Старые v1 hashes нельзя исправить задним числом. Recovery старого теста удаляет
только его ресурсы при stopped service и пустом namespace, сохраняет v1 hashes
отдельно и явно оставляет historical_snapshot_fully_verified=false. Новый запуск
требует нового baseline. Для v2 неустранённый drift блокирует запуск.

ExecStopPost проверяет завершение своего systemd cgroup и descendants (кроме
самого cleanup); CAP_SYS_PTRACE не требуется. Неизвестный cgroup/live service
process запрещает удаление namespace. Standalone recover дополнительно
проверяет namespace PID с обычными root-правами до scoped cleanup. Удаление
посторонних namespace/firewall rules запрещено.

## Исправление индивидуального revoke

В локальном реальном Linux probe с private mount namespace воспроизведено:
`ip netns exec` из внешнего процесса не видит созданный внутри sandbox bind
mount, хотя общий `/run/netns` содержит его placeholder. Это объясняет отказ
внешнего revoke; тот же pinned движок без этой границы удаляет peer нормально.

Отзыв теперь обращается непосредственно к root-owned pathname UAPI socket
`/run/amneziawg/v11awg.sock` через pinned `awg`, без входа в namespace. Набор
capabilities и unit не расширяются. Проверяются baseline/ready/input ownership,
socket type/owner/permissions/inode, hash бинарника и точный единственный peer
(либо уже пустой набор). Неожиданные peers закрывают операцию. Пустой результат
подтверждает удаление конфигурации, но traffic_cutoff_verified остаётся false
до отдельной физической пробы. Повторный отзыв проверен локально.

Controller сохраняет только разрешённые stage/failure labels при ошибке runner,
вместо потери причины за i011_control_refused. stdout/stderr/key/profile не
выводятся. Новая privileged версия требует новой установки и hash-bound grant;
на момент локальной проверки обновлённая версия ещё не была принята на VPS.
Последующая VPS-проверка прошла: active engine, peer removed, физический Pixel
перестал передавать IPv4/IPv6, повтор revoke успешен; receipt сохранён отдельно.

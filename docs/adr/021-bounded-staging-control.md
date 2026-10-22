# ADR-021: временное управление I-011 без повторной передачи sudo-пароля

Status: Accepted for staging test only by owner request, 2026-10-19.

## Запрос и граница

Владелец попросил один раз ввести sudo-пароль локально и затем продолжить
проверки автономно. Пароль/профиль не помещаются в чат, файлы или логи. Вместо
хранения пароля добавляется временное sudoers-разрешение **только** для
root-owned `/opt/project-vpn/acceptance/i011_control.py` на одобренном staging.
Клиент POC не имеет пароля профиля: private key создаётся на Android.

Первоначальная установка выполняется Ansible с локальным --ask-become-pass.
`-GrantControl` устанавливает код/unit, создаёт 45-минутную lease, проверяет
sudoers через visudo, ставит systemd expiry timer и запускает только read-only
диагностику. Секреты sudo не сохраняются. Правило не разрешает Python/shell,
редакторы, произвольные пути или systemctl напрямую.

## Ограничения helper

Isolated Python shebang `#!/usr/bin/python3 -I`, фиксированные программы и
очищенное окружение дочерних процессов. Единственный аргумент из allowlist:
preflight, diagnose, start, status, stop, revoke, recover, key, profile, receipt, expire.
Нет произвольного shell/командного текста, загрузки и исполнения нового кода,
изменения sudoers, deployment или сетевых параметров из пользовательского ввода.
`key` читает только канонический public key через stdin при остановленном VPN;
`profile` — отдельный секретный канал передачи готового profile в память клиента,
его stdout **никогда не направлять в чат/tool output/обычный лог**.

Каждая операция требует UID vpn-deploy, фактический staging IPv4, root-owned
неизменяемые для пользователя source/unit/lease, совпадение SHA-256 трёх скриптов
и unit. Lease привязана к boot ID; проверяются wall clock и monotonic time.
Максимум 2700 секунд с выдачи, без автоматического продления. После reboot
tmpfs lease отсутствует и доступ закрыт, даже если sudoers-файл остался.
Изменение привилегированного кода инвалидирует grant и потребует нового
локального sudo: это намеренная граница, не обещание любого последующего root-доступа.

При expiry systemd вызывает фиксированный expire: stop I-011, удаление **только**
этого sudoers-файла и lease. Даже при ошибке stop удаление grant выполняется
в finally. Каждый VPN service независимо ограничен 600s (ADR-020). Receipt
публикует только fixed error/status, booleans rollback и InvocationID, без
raw journal/config. Владелец может вызвать expire раньше. До первых live
проверок timer/revocation их работа не заявляется измеренной.

## Исправление container_routes

Локальная реальная systemd-проверка воспроизвела EACCES=13 при чтении
`/proc/PID/ns/net` из service с урезанными capabilities. Официальный
[namespaces(7)](https://man7.org/linux/man-pages/man7/namespaces.7.html)
описывает ptrace access check для таких ссылок. Локальный bind-mounted
namespace handle позволил прочитать маршруты с прежним набором capabilities.

Runner использует Docker `NetworkSettings.SandboxKey`: только числовой hex
basename из `/var/run/docker/netns` или `/run/docker/netns`, канонический путь
в `/run/docker/netns`, root ownership и отсутствие writable/symlink parents.
Состояние контейнера ID/PID/start/SandboxKey и inode namespace сверяются до/после
read-only probe. Любой restart/path drift, неизвестный namespace или пересечение
подсетей закрывает запуск. CAP_SYS_PTRACE не добавлен, sandbox не ослаблен,
container exec и чтение конфигов не используются. На VPS fresh sandbox preflight,
forced-stop и фактический 600s deadline прошли с семью baseline checks.

## Фактическое завершение доступа 2026-10-19

Через grant проведена физическая Android приёмка handshake/egress. Первый
peer revoke вернул i011_control_refused. После нового локального ввода sudo
исправленный direct UAPI revoke прошёл при работающем движке; прекращение
трафика Pixel, повтор и полный cleanup подтверждены. В конце grant отозван вручную
фиксированным expire, повторный sudo -n потребовал пароль. Автоматический
45m expiry не ожидался и не объявляется проверенным. Работа нового privileged
кода при следующем запуске потребует нового локального разрешения владельца.

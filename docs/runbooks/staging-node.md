# I-010: установка одного тестового узла

## Текущий статус

Staging VPS `45.10.166.106` одобрен владельцем. Бинарники и лицензии размещены
под `vpn-deploy` с интерактивным sudo; повторный прогон дал `changed=0 failed=0`.
2026-10-19 отдельно проверены удалённые хеши, права, `current` и `awg --version`.
Полный отчёт: [verification-i-010](../verification-i-010.md).
I-010 завершён по ADR-018: на самом VPS прошёл isolated peer acceptance,
повтор и failure cleanup; временный runtime убран. Постоянный tunnel, внешний
endpoint и egress остаются непроверенными. Финальный receipt сохранён.

## Диагностика существующего VPN перед сетевыми изменениями

На узле обнаружены Docker, процесс `amneziawg-go` и bridge `amn0`.
Live diagnostic владельца завершился `ok=3 changed=0 failed=0`:
`amnezia-awg2` → UDP 34053, `amnezia-wireguard` → UDP 34610.
Оба контейнера сохраняем. Host routes занимают `172.17.0.0/16` и
`172.29.172.0/24`; это не полный inventory внутренних tunnel subnets.
Расширенная диагностика также завершилась `ok=5 changed=0 failed=0`:
в обоих контейнерах используется `10.8.1.0/24`. Ниже команда для воспроизведения;
сейчас повтор не нужен. Конфигурации и исполняемые файлы контейнеров не читаются.
План изоляции будущего peer: [ADR-017](../adr/017-staging-network-isolation.md).

В PowerShell выполнить одну команду (диагностика читает только метаданные):

```powershell
wsl -d kali-linux --cd 'C:\Users\JetGame\OneDrive\Документы\ChatGPT\Vpn (пока без названия)' -- /tmp/vpn-ansible-2.21.5/bin/ansible-playbook -i infra/ansible/inventory.yml infra/ansible/staging-network-preflight.yml --extra-vars '@infra/ansible/release.yml' --extra-vars 'vpn_approved_host_address=45.10.166.106' --ask-become-pass
```

На `BECOME password` ввести пароль `vpn-deploy` локально. Не передавать пароль
в аргументах или чате. Ожидается `changed=0 failed=0`, пять прежних блоков и
новый `Display complete selected network inventory` с JSON. Пришлите новый
блок и recap. При ошибке скрытого `Read selected Docker subnet and namespace
routing metadata` остановитесь и передайте название task и recap: не включайте
`-vvv` и не снимайте `no_log`. Старые пять блоков не заменяют полный новый JSON.
Конфиги, ключи и полный inspect не нужны.
Если `/tmp/vpn-ansible-2.21.5` отсутствует после перезагрузки, остановиться:
восстановление закреплённого controller environment — отдельный локальный шаг.

Проверяются явный адрес, approval, staging environment, единственный host и
non-root SSH user. Команды чтения выполняются даже при `--check`. Любая ошибка
останавливает процедуру; отсутствие вывода не трактовать как отсутствие VPN.
Доступ к Docker socket пользователю не добавлять: Docker group даёт широкие права;
для этой проверки достаточно интерактивного sudo.

## Повторное размещение на одобренном стенде

1. Записать в локальном `infra/ansible/inventory.yml` **только** одобренный staging
   hostname/IP и не-root `ansible_user`. Не помещать в файлы/чат пароль, SSH private
   key, node credential или VPN private key. `sudo` для Ansible `become`
   фактически даёт root-права на время запуска: нужен отдельный пользователь
   только на этом staging host, с запросом sudo password, без production access.
2. Получить review для binary build из ADR-003/матрицы совместимости и создать
   локальный `infra/ansible/release.yml` с путями и SHA-256 двух Linux binaries и
   upstream LICENSE/COPYING. Указать точные source commits из ADR-003,
   `vpn_environment: staging`, `vpn_staging_approved: true` и
   `vpn_approved_inventory_name` равный имени inventory host и
   `vpn_approved_host_address: 45.10.166.106`. Сам artifact playbook также
   сверяет фактический inventory address с этим явно одобренным адресом. Не подставлять
   guessed hashes или binaries из неподтверждённого release.
3. На Linux Ansible controller с `ansible-core==2.21.5` выполнить syntax check и
   просмотреть inventory. Эти команды не подключаются к VPS:

```text
ansible-playbook -i infra/ansible/inventory.yml infra/ansible/staging-node.yml --syntax-check
ansible-inventory -i infra/ansible/inventory.yml --list
```

4. После явного approval на указанный staging host выполнить playbook с локальной
   авторизацией SSH и ограниченным sudo. При необходимости Ansible запрашивает
   sudo пароль в терминале, не в аргументах команды и не в чате:

```text
ansible-playbook -i infra/ansible/inventory.yml infra/ansible/staging-node.yml --extra-vars @infra/ansible/release.yml --ask-become-pass
```

5. Записать итог Ansible без секретов, проверить четыре хеша на host и что
   `current` указывает на ожидаемый release. Повторить ту же команду: после первого
   прогона не должно быть изменений. Проверить список служб и UDP listeners; сравнивать с сохранённым снимком
   до изменения. Без исходного снимка не утверждать отсутствие новых портов.
   UDP 34053/34610 заняты существующими контейнерами AWG/WireGuard. До конфигурации peer/VPN не заявлять
   новый egress IP.

При ошибке preflight не обходить проверку `vpn_staging_approved`, ревизий или
хешей. При ошибке копирования симлинк не переключается. Для отката повторно
выбрать старый проверенный релиз из сохранённого bundle; не удалять каталог
текущего релиза до подтверждённой проверки. Любую сетевую настройку делать
отдельным reviewable шагом после узлового ADR и I-011 prerequisites.

## Локальная проверка pinned AWG без VPS

В PowerShell из корня проекта (только локальный WSL):

```powershell
wsl -d kali-linux -u root --cd (Get-Location).Path -- python3 scripts/awg_local_smoke.py --artifacts-dir .cache/i-010/artifacts
```

Это реальный обмен двух Linux peers в свежих namespaces, без интернета и NAT.
Root относится к локальному WSL, удалённые root credentials не используются.
Нужны имеющиеся TUN/ip/ping/findmnt и оба ранее собранных pinned artifacts;
скрипт ничего не скачивает. Проверяет SHA-256, создаёт runtime keys в private
`/run` tmpfs, не выводит ключи/config, после завершения убирает свои ресурсы.
Успех: `passed`, `handshake`, `bidirectional_ping`, `peer_removal_blocks_ping`,
`cleanup_complete`, `host_ipv4_routes_unchanged` равны true.

Для проверки аварийной очистки к той же команде добавить по одному
`--inject-failure-after-setup` или `--inject-failure-with-key-file`.
В этих двух сценариях exit=1 и `passed=false` ожидаемы; `cleanup_complete` и
`host_ipv4_routes_unchanged` должны остаться true. При cleanup=false не повторять
тест до проверки оставшихся локальных ресурсов; не удалять namespace по маске.
SIGKILL/сбой WSL обойти обработчик очистки могут, поэтому это локальная лаборатория.

По умолчанию script допускает только WSL. Закрывающий Ansible acceptance
использует его на одном явно одобренном VPS с отдельным staging flag и OS/IP
checks (см. следующий раздел). Фиксированный test profile не выдаётся пользователям. Staging tunnel, Android, DNS/IPv6 policy, внешний egress и
API revoke остаются отдельными проверками I-010/011/012.

## Финальная приёмка I-010

Сначала прочитать [ADR-018](../adr/018-staging-node-acceptance.md).
Первый live run остановился на `start_pinned_awg`: Ubuntu `/run` имеет noexec.
Cleanup подтвердился. Runner исправлен, последующий VPS run прошёл
`ok=48 changed=2 failed=0`; receipt проверен, I-010 завершён. Ниже команда
для воспроизведения; повторять сейчас не требуется.
Он устанавливает artifacts дважды, запускает реальные AWG peers на VPS только
в изолированных namespaces и проверяет rollback. Release остаётся установленным,
тестовые peers/keys/interfaces удаляются. Это не публичный endpoint или I-011.

В PowerShell выполнить одну команду:

```powershell
wsl -d kali-linux --cd 'C:\Users\JetGame\OneDrive\Документы\ChatGPT\Vpn (пока без названия)' -- /tmp/vpn-ansible-2.21.5/bin/ansible-playbook -i infra/ansible/inventory.yml infra/ansible/staging-acceptance.yml --extra-vars '@infra/ansible/release.yml' --extra-vars 'vpn_approved_host_address=45.10.166.106' --ask-become-pass
```

На `BECOME password` вводится только локально пароль `vpn-deploy`.
Подтверждение прав уже дано для staging; это не запрос новых production прав.
`--check` намеренно запрещён: он не доказывает исполнение тестового узла.
SSH host-key checking не отключать. Если controller `/tmp/vpn-ansible-2.21.5`
исчез после перезагрузки, восстановить закреплённый controller environment;
не заменять команду на непроверенный install latest.

Ожидается финальная строка `I-010 acceptance passed` и recap `failed=0,
unreachable=0`. Два injected-failure tests ожидаемо имеют process exit=1,
но сам playbook проверяет нужные stage/cleanup и считает их успешными только
при полном восстановлении состояния. Нельзя обходить упавший assert.
Проверяются identity/running существующих контейнеров, firewall, routes/links,
namespace list, удаление transient units. Состояние их VPN-сессий не проверяется.

Новый датированный receipt появится в
`docs/evidence/i-010-staging-acceptance.json` только после всех четырёх runs
и проверок. Для продолжения достаточно прислать `PLAY RECAP`; агент прочитает
receipt из общей папки и сопоставит timestamp/результаты. При неуспехе прислать
название failed task и recap; не добавлять `-vvv` и не выводить конфиги.

Receipt от 2026-10-19T12:03:34Z проверен: bounded test-node scope ADR-018
завершён. Внешний ingress/Android/egress требуют I-011. При будущих повторах
оценивать новый receipt, а не считать старый доказательством нового запуска.
Если unit упирается в deadline, Python обрабатывает SIGTERM; systemd ограничивает
длительность cgroup. После SIGKILL/сбоя ядра возможны остаточные isolated
namespaces/tmpfs files. Не перезапускать на cleanup failure и не удалять по
маске: сначала определить точные ресурсы неуспешного теста. Existing VPN,
firewall, маршруты и service containers для исправления теста не менять.

# I-010: проверка развёртывания тестового узла на staging

Обновлено: 2026-10-19. Статус: **I-010 выполнен в границе bounded staging test-node acceptance (ADR-018)**.
Публичный ingress, Android и внешний egress ещё не проверены. Ниже сохранена
история подготовки и первого отказа; итог успешной приёмки — в последнем разделе.

## Что подготовлено

- Ansible playbook для одного явно одобренного staging host на Ubuntu 24.04 amd64.
- Предзапуск проверяет разрешение, имя host, непривилегированного SSH user,
  закреплённые ревизии исходников и локальные SHA-256 до подключения к host.
- На host предусмотрены проверка платформы и TUN, копирование двух заранее
  собранных бинарников и файлов лицензий в каталог релиза, повторная проверка
  SHA-256 и выбор проверенного релиза через `current`.
- Ограничения и процедура будущего запуска записаны в ADR-015 и runbook.

## Локальные проверки

- `ansible-core==2.21.5` установлен в изолированном Python окружении Kali WSL.
  Для создания окружения локально установлен пакет `python3.13-venv`.
- `ansible-playbook -i infra/ansible/inventory.example.yml infra/ansible/staging-node.yml --syntax-check`
  — прошёл; Ansible сообщил лишь, что пример inventory не содержит hosts.
- Запуск с фиктивным локальным inventory и `vpn_staging_approved=false` остановился
  на первой проверке, `changed=0`.
- Запуск с фиктивным локальным inventory, разрешением и отсутствующими binary files
  остановился на проверке локальных файлов до чтения remote facts, `changed=0`.
- SHA-256 локально сохранённых upstream `LICENSE`/`COPYING` совпали с константами
  playbook.

## Сборка артефактов для одобренного staging IP

Владелец подтвердил, что `45.10.166.106` — staging VPS и разрешил
развёртывание. Для доступа подготовлен отдельный не-root SSH user `vpn-deploy`; вход по ключу
проверен. Локальные `inventory.yml` и `release.yml` исключены из Git.
Sudo-пароль вводит владелец в своём терминале.

- В Kali WSL использован Go `1.25.14` для Linux amd64. SHA-256 официального
  `go1.25.14.linux-amd64.tar.gz` —
  `a21ae5633a269bcd7e90cf767e48225633795e99d831742cbf3397064fee7712`,
  совпал с [каталогом Go](https://go.dev/dl/). Архив установлен только в `/tmp`.
- `amneziawg-go` собран из clean Git commit
  `1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1` через
  `GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build
  -mod=readonly -trimpath -buildvcs=true`. `go version -m` подтверждает этот
  commit, Go version и отсутствие CGO; SHA-256 binary —
  `84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe`.
- `awg` собран из clean Git commit
  `ee0f0a9aa34ff0a0da4b3433b9512781cfe02843` через `make` и
  `musl-gcc` 1.2.6 с `LDFLAGS=-static`. `awg --version` выдал
  `amneziawg-tools v3.1.20260812`; `readelf` не показал dynamic interpreter.
  SHA-256 binary —
  `4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce`.
- `go mod verify` прошёл. `go test ./...` с обычным CGO прошёл для всех
  протестированных packages. Отдельный запуск с `CGO_ENABLED=0` завершился
  ошибкой сборки `outline` (`undefined: lookupCNAME`); этот package не входит в
  собранный серверный binary по `go version -m`. Не считать полный CGO=0
  test run успешным.
- Оба binary и upstream LICENSE/COPYING сохранены только в игнорируемой
  `.cache/i-010/artifacts/`. SHA-256 после копирования совпали; Ansible syntax
  check с настоящим локальным inventory/release manifest прошёл. Первоначальный
  preflight ожидаемо отказал при пустом `ansible_user`, `changed=0` и
  `unreachable=0`; на этом первоначальном шаге подключения к VPS не было.
- Повторная сборка Go из того же checkout дала тот же SHA-256. `make clean`
  и повторная сборка tools с теми же параметрами также дали тот же SHA-256.
  Это повтор на одном локальном build host, не независимая воспроизводимость.

Это проверка происхождения и сборки, не security clearance: advisory triage,
полный SBOM, независимая clean сборка и исполнение на Ubuntu 24.04 ещё не сделаны.
Утилита `awg` использует статический musl; обязательства распространения
AWG tools GPL-2.0 и musl MIT следует проверить для публичного выпуска.

Фиктивный inventory использовал `ansible_connection: local`; внешние hosts не
подключались. Эти проверки подтверждают синтаксис и два отказа preflight, а не
полный успешный прогон playbook.

## Фактическое размещение и повторная проверка 2026-10-19

В предыдущем чате владелец передал результаты двух запусков playbook:
первый — `ok=13 changed=3 unreachable=0 failed=0`, повторный —
`ok=13 changed=0 unreachable=0 failed=0`. Это результат из терминала
владельца; сегодня playbook повторно не запускался.

Сегодня выполнена отдельная read-only SSH проверка под `vpn-deploy` с
`BatchMode=yes` и `StrictHostKeyChecking=yes`, без sudo и изменений на сервере:

- Ubuntu 24.04, x86_64; `/dev/net/tun` — символьное устройство.
- `current` указывает на `/opt/project-vpn/releases/9ce555ad32d1d80b61322da0b59705854af5521e7a6a0db8d0913cd03ae0848c`.
- Все четыре SHA-256 совпали с указанными выше бинарниками и константами
  LICENSE/COPYING в playbook.
- Бинарники принадлежат `root:root`, права `0755`; лицензии — `root:root`,
  `0644`. Симлинк `current` принадлежит `root:root`; его обычные Linux права
  `0777` не означают права записи в целевой каталог.
- `awg --version` на VPS: `amneziawg-tools v3.1.20260812`.
- В списке running systemd services нет совпадений по
  `amnezia|wireguard|wg-quick|project-vpn`. Это проверка имён служб,
  не доказательство отсутствия любого VPN-процесса.
- `ss -H -lun` показывает DNS/DHCP и wildcard UDP listeners 34053/34610.
  Их владельцы и назначение в этой проверке не установлены; исходного снимка
  до установки нет, поэтому отсутствие новых UDP listeners не подтверждено.
  Существующие службы и порты не изменялись.

Секреты, конфигурация peer и private keys в результат проверки не включались.

## Непроверенное и условия завершения

Дополнительная read-only проверка 2026-10-19 обнаружила running Docker/containerd,
процесс `amneziawg-go` (читалось только имя executable), bridge `amn0`, veth,
IPv4 forwarding=1 и IPv6 forwarding=0. Это свидетельствует о существующем
окружении, которое нужно сохранить; handshake/egress этим не проверены.
`vpn-deploy` не имеет доступа к Docker socket. Владелец попросил сначала
выяснить назначение существующего VPN. Не следует считать сервер пустым.

Подготовлен `infra/ansible/staging-network-preflight.yml`: read-only socket
owners/routes/container metadata через интерактивный sudo. На 2026-10-19:

- Ansible 2.21.5 `--syntax-check` — pass.
- Шесть negative runs на явно фиктивном локальном inventory — pass: отказ при
  отсутствии approval, неверном адресе, root user, non-SSH transport, неверном
  inventory name и нескольких hosts. Все остановились на первом assert до
  remote probes; это не проверка реального sudo или Docker metadata.
- Владелец выполнил live diagnostic и передал полный вывод: `ok=3 changed=0
  unreachable=0 failed=0`. Результат проверен по приложенному тексту; команды
  с sudo в этой сессии агентом повторно не запускались.
- UDP 34053 принадлежит docker-proxy и опубликован контейнером `amnezia-awg2`
  (образ `amnezia-awg2`); UDP 34610 — контейнером `amnezia-wireguard`
  (образ `amnezia-wireguard`). Оба опубликованы на IPv4/IPv6 wildcard addresses.
  Это фактические port mappings, но не проверка внешней достижимости/handshake.
- Main routes включают `172.17.0.0/16` через `docker0` и `172.29.172.0/24`
  через `amn0`. Docker перечислил `amnezia-dns-net`, `bridge`, `host`, `none`.
  По одному имени нельзя утверждать, к какой сети подключён контейнер.
  На том первом этапе внутренние tunnel routes ещё не были исследованы;
  результат расширенного запуска записан ниже.
- На host есть IPv6 default route. Клиентская IPv6/DNS policy ещё не проверена.
- Предупреждение Ansible о discovered Python `/usr/bin/python3.12` не остановило
  диагностику. Interpreter не менялся. Сеть и контейнеры не изменялись.
- [ADR-017](adr/017-staging-network-isolation.md) фиксирует последовательность
  изоляции peer, ключей, маршрутов и отката. Конкретные порт/подсеть пока не
  назначены; runnable tunnel service не создавался.

- Фактический rollback и независимая clean сборка не проверены.
- Назначение UDP 34053/34610 установлено; пользователей и активность их
  туннелей диагностика не определяет. Снимок после установки не доказывает
  отсутствие новых портов относительно состояния до установки.
- Для нашего отдельного тестового туннеля нет конфигурации, ключей, peer lifecycle и node agent; нет
  измерений трафика, сетевой доступности и реального egress IP.

Следующий шаг I-010 — отдельный проверяемый план key/config lifecycle,
least privilege, firewall/NAT, rollback и тестового peer с учётом ADR-015/016
и I-011 prerequisites. Установка бинарников сама по себе не доказывает работу VPN.

## Расширение read-only inventory: подготовлено 2026-10-19

Изменены `staging-network-preflight.yml`, добавлены standalone stdlib helper
`infra/ansible/files/staging_network_inventory.py` и явные synthetic tests
`infra/ansible/tests/test_staging_network_inventory.py`. `pyproject.toml` включает
helper в общий mypy, а тесты — в общий pytest/Ruff. Новых зависимостей нет.
Helper читает только Docker IPAM/attachments и host/container routes/rules,
не запускает container binaries и не читает ключи, env, конфиги или трафик.
Предусловия и пределы snapshot описаны в ADR-017.

Фактические проверки:

- `uv run --frozen pytest -q -rN`: **75 passed, 166 skipped**. Все 17 новых
  infrastructure tests прошли на fake command results без Docker/SSH. PostgreSQL
  tests пропущены без тестовой БД; это не повторная проверка backend на PostgreSQL.
- `uv run --frozen mypy`: success, 55 source files.
- `uv run --frozen ruff check .`: pass.
- Formatter для двух новых Python files: pass. Общий `ruff format --check .`
  обнаружил существующее несоответствие в `services/api/tests/test_migrations.py`
  (две записи assert с migration version). Этот файл не менялся: дефект вне I-010.
- `uv lock --check`: pass.
- Расширенный playbook `--syntax-check` Ansible 2.21.5: pass.
- Шесть предзапусковых отрицательных сценариев повторены: все отказали на первом
  assert до SSH/remote probes. Отдельный local Ansible transport test подтвердил
  передачу literal Docker Go templates через helper lookup без выполнения probes.
- SSH под `vpn-deploy` подтвердил пути `/usr/bin/python3`, `/usr/bin/docker`,
  `/usr/bin/nsenter`, `/usr/sbin/ip`. Проверка `sudo -n /usr/bin/true` отказала:
  требуется пароль. Пароль не запрашивался в чате и не передавался агенту.

**Позднее владелец выполнил расширенный live helper:** `ok=5 changed=0
unreachable=0 failed=0`. Полный приложенный вывод разобран как JSON;
`schema_version=1`, оба контейнера присутствуют. В каждом namespace найден
route `10.8.1.0/24` (AWG `awg0`, WireGuard `wg0`); Docker IPAM — `172.17.0.0/16`
и `172.29.172.0/24`. Минимальная сводка и hash входного текста:
[receipt](evidence/i-010-network-summary.json). Сеть, containers, peers и firewall не менялись. Порт и подсеть нового
peer не назначены. I-010 и реальный tunnel/egress остаются незавершёнными.

## Фактический локальный AWG runtime spike 2026-10-19

Добавлен `scripts/awg_local_smoke.py` — только локальный WSL, root, pinned hash
checks, две временные сети без внешних маршрутов. Репозиторий не содержит
ключей/секретных test fixtures: штатный awg создаёт runtime keys, private files
живут в 0700 tmpfs directory, с правами 0600, не выводятся. Test profile использует
AWG-specific J/S/H и header protection; это не production/DPI clearance.

- Два положительных реальных запуска: handshake, двусторонний ping, ненулевой
  rx/tx, server peer removal и последующий отказ ping — **pass**.
- Два намеренных сбоя (после network setup; при существующем key file и двух
  процессах): ожидаемый exit=1, cleanup_complete=true, host routes unchanged.
- Изменённый artifact: отказ на prerequisites до создания namespace — **pass**.
- Независимый осмотр после тестов: 0 оставшихся test namespaces, root links,
  sockets и tmpfs directories. Совпадение root IPv4 routes проверено скриптом.
- Ruff format/check нового script — pass; общий lint — pass; strict mypy —
  success, 56 files. Исходное formatter отклонение test_migrations.py остаётся
  отдельно описанным дефектом вне scope.
- Тесты runbook воспроизводятся одной локальной WSL командой. Никакой staging
  процесс/peer не запускался; это **не** Android, внешний egress, DNS/IPv6 leak
  test, работа квоты или I-012 API revoke. Все ресурсы были в локальном WSL.

Кандидат `10.203.71.0/30` не пересекается с полученным server/container inventory
и текущими Windows non-default routes (проверка stdlib ipaddress). Это только
предложение для будущего staging Linux peer, не применённая конфигурация.
До включения staging tunnel нужны свежий port/firewall preflight, минимальный
service/config/rollback diff и ADR-003 security prerequisites. I-010 не закрыт.

## Заключительная приёмка: подготовлена, VPS run ожидается

Каноническая граница, acceptance checklist и remaining limitations:
[issue I-010](issues/I-010.md), [ADR-018](adr/018-staging-node-acceptance.md).
Новый `infra/ansible/staging-acceptance.yml` проверяет controller guard до SSH,
дважды выполняет artifact deployment, устанавливает root-owned acceptance runner
и исполняет 4 bounded transient systemd services на VPS. Каждый run проверяется
до следующего; expected failure stage, cleanup и неизменность existing state
обязательны. После последнего test проверяется отсутствие transient units.
Receipt создаётся локально только после всех проверок и содержит timestamps,
booleans и scope, без keys/configs/peer identifiers.

Проверка Linux build security:

- `govulncheck@v1.9.0 -mode=binary`: exit=0, **0 symbol/package findings**;
  42 unique advisory IDs только на module level. Raw scan сохранён в ignored
  cache; минимальная сводка, native scanner SBOM и DB timestamp —
  [audit](evidence/i-010-go-linux-audit.json).
- Для scanner в изолированном `/tmp` проверен официальный SHA-256 Go 1.26.9.
  Product binaries не пересобирались: тот же pinned Go 1.25.14 binary/hash.
  BSD-3-Clause scanner — отдельный admin tool, не bundled library.
- Binary scan не является formal audit, Android acceptance, C/musl scan или
  полным release SBOM. Утверждение безопасности production не делается.

Локальные проверки заключительного пакета:

- 12 новых tests с явно fake OS/command metadata проверили отказ на не-root,
  неизвестной/неуказанной цели, неверной ОС, неназначенном IP, WSL/staging flag,
  остановленных existing containers и отсутствие raw metadata в snapshot.
- Общий `pytest -q -rN`: **87 passed, 166 skipped** (PostgreSQL не подключён).
- Общий Ruff lint и mypy: pass, 56 source files. Новый/изменённый Python format:
  pass. Старое форматирование test_migrations.py вне I-010 не исправлялось.
- Ansible `--syntax-check`: pass. Восемь negative controller runs (empty/multiple
  inventory, no approval, wrong address/root/name/transport/check mode) остановили
  ВЕСЬ playbook до SSH, включая imported deployment plays.
- Synthetic local Ansible harness проверил настоящие result assertions:
  четыре ожидаемых результата принимаются; cleanup=false немедленно останавливает
  последующие runs. Это тест orchestration, не evidence реального VPS.
- Реальный local WSL transient systemd run с теми же capability/filesystem
  properties прошёл handshake/packets/remove/cleanup. Дополнительный deadline
  RuntimeMaxSec=2s фактически вызвал SIGTERM и подтвердил cleanup до удаления cgroup.
- Read-only SSH подтвердил на VPS `/run` tmpfs, TUN и нужные пути Python/systemd/
  ip/ping/iptables/ip6tables/docker. Sudo для live acceptance требует локального
  ввода владельца; пароль агенту не передаётся.

**Текущий итог: I-010 не объявлен завершённым.** Всё для завершающего live run
подготовлено и проверено; осталось выполнить одну команду раздела «Финальная
приёмка I-010» runbook. Только свежий receipt с actual VPS results позволит
отметить оставшиеся пункты issue. Никакой публичный порт/peer в этом пакете
не открывается. Следующий issue после bounded node acceptance — I-011.

Artifact playbook тоже усилен проверкой `vpn_approved_host_address` и совпадения
inventory address с текущим approved IP: imported deployment нельзя направить
на другой адрес, пропустив controller play через CLI limit. В ignored local
release.yml добавлен уже одобренный адрес; секреты не создавались и не менялись.

## Первый завершающий VPS run: отказ на noexec, исправление

Владелец передал полный вывод: controller ok=3 changed=0 failed=0;
staging ok=33 changed=2 unreachable=0 failed=1. Installation/imported plays
прошли, acceptance code размещён; первый runtime остановился на start_pinned_awg
до keys/config. Все snapshot comparisons, cleanup_complete — true; дальнейшие
runs не выполнялись, success receipt не создан.
[Минимальный failure receipt](evidence/i-010-staging-failure-2026-10-19.json).

Read-only SSH подтвердил `/run` tmpfs rw,nosuid,nodev,noexec; `/tmp` находится
на executable ext4. Причина воспроизведена локально с NoExecPaths=/run:
старый runner точно так же отказал на start_pinned_awg с полной очисткой.
Исправление: только keys/config в `/run` (0700 parent, 0600 files); public
binaries копируются в другой 0700 `/tmp` directory с повторным SHA-256 и X_OK.
Cleanup охватывает оба каталога. NoExecPaths=/run добавлен в staging unit;
host mounts, firewall, containers и настройки безопасности не меняются.

После исправления реальные local WSL systemd tests с NoExecPaths=/run:
positive handshake/packets/peer removal, setup failure cleanup, key-file failure
cleanup и RuntimeMaxSec deadline cleanup — все прошли. Это не successful VPS
rerun; его всё ещё нужно выполнить с локальным sudo вводом владельца.
I-010 остаётся незавершённым до нового успешного live receipt.

Проверки после noexec fix: общий pytest — **90 passed, 166 skipped**; общий
Ruff lint и mypy (56 files) — pass; formatter изменённых Python files — pass;
Ansible syntax check — pass. PostgreSQL integration tests не запускались без БД.

## Итог: I-010 завершён 2026-10-19

Новый [live receipt](evidence/i-010-staging-acceptance.json) прочитан и проверен
по всем predicates, source/schema/host, числу runs и timestamps. Он соответствует
переданному владельцем финальному выводу: staging `ok=48 changed=2 unreachable=0
failed=0`, localhost `ok=3 changed=0 failed=0`. Время UTC:
12:01:52 → 12:03:34 (15:01:52 → 15:03:34 МСК).

- Artifact installation и повторный import прошли; immutable release сохранён.
- На настоящем VPS дважды проверены handshake, двусторонние пакеты и отказ
  ping после удаления server peer.
- Оба injected-failure runs дали ожидаемые стадии ошибки и полную очистку;
  `passed=false` в этих двух runs — ожидаемый результат, не failure приёмки.
- Во всех 4 runs unchanged=true для existing containers, firewall IPv4/IPv6,
  host routes IPv4/IPv6, link names и preexisting namespaces.
- `cleanup_complete=true` во всех runs; `transient_units_removed=true`.
- `changed=2` относится к обновлению runner после noexec fix и записи receipt,
  а не к изменению пользовательских контейнеров/маршрутов/firewall.

I-010 закрыт по [checklist](issues/I-010.md) и границе ADR-018. Установленный
release остаётся на узле; test runtime/keys/interfaces убраны. Постоянный
пользовательский tunnel, WAN reachability, Android/egress, DNS/IPv6 leak tests,
quota enforcement и API revoke этим не заявляются. Existing container identity
и selected network state сохранены; пользовательская VPN-session health
отдельно не проверялась. Native security/licensing и старый formatter defect
остаются описанными ограничениями, а не результатами этого acceptance.

Следующий issue — I-011: актуализировать build prerequisites и совместимую
Android integration, затем подготовить ограниченный внешний endpoint и проверить
физический телефон/egress/DNS/IPv6. Автоматически I-011 не начинался.

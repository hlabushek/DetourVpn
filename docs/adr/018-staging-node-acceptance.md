# ADR-018: приёмка одного staging узла I-010

Status: **Implemented and verified — bounded I-010 staging acceptance**, 2026-10-19.

## Контекст и граница

ROADMAP задаёт для I-010 «One test node deploy / Repeatable provisioning»,
а для I-011 — Android native POC и реальный egress IP. На staging уже стоят
наши pinned binaries и два существующих контейнера Amnezia. Внешняя
совместимость и безопасность native bundle ещё имеют отдельные I-002/I-011 gates.

Для закрытия I-010 требуется доказать запуск реального AWG на самом VPS и
воспроизводимость provisioning, а не только положительный локальный mock.
Решение: заключительный VPS acceptance использует два свежих namespace,
связанных только veth. Это настоящий протокол с runtime keys, handshake,
пакетами и удалением peer. Он не доказывает WAN ingress, Android или внешний
egress; I-010 не будет представлен как готовый пользовательский VPN.
Файлы release остаются установленными; transient runtime полностью убирается.
Endpoint/firewall/NAT и persistent peer lifecycle для физического клиента
остаются необходимыми следующими шагами I-011/012.

## Выполнение

`staging-acceptance.yml` сначала на controller проверяет ровно один одобренный
host/address/environment/non-root SSH identity, запрет check mode. Ошибка
прерывает весь playbook до SSH, включая последующие imported plays.
Затем дважды исполняется существующий artifact deployment и размещается
root-owned acceptance runner. Каждый тест работает через transient systemd
service с RuntimeMaxSec=75s, TimeoutStopSec=20s, KillMode=mixed и `--collect`.

Runner по умолчанию по-прежнему допускает только локальный WSL. На VPS нужен
явный `--approved-staging-address=45.10.166.106`, Ubuntu 24.04 amd64 и фактически
назначенный интерфейсу этот адрес. Нет root SSH или production credentials.
Root execution нужен только этому короткому административному namespace test:
CAP_SYS_ADMIN для mount/netns, CAP_NET_ADMIN для TUN/veth, CAP_NET_RAW для ping.
NoNewPrivileges, PrivateTmp, ProtectHome, ProtectSystem=strict, запись только
в `/run` и private temporary directories в дополнение к API filesystems;
NoExecPaths=/run сохраняет запрет исполнения из key storage. Это не произвольный command endpoint
и не production daemon sandbox; root внутри ограниченного теста остаётся
сильной привилегией, поэтому запускается только reviewed fixed code.

Ключи создаются штатным awg, config — 0600 в 0700 tmpfs directory, silent logging,
никакого вывода private/public keys или config. Перед исполнением public binaries копируются в отдельный 0700 executable
каталог `/tmp` (PrivateTmp), повторно проверяются по SHA-256 и `os.access(X_OK)`.
Ключи/config не помещаются в этот каталог. `/run` остаётся tmpfs для keys и
может иметь noexec; параметры монтирования хоста не меняются. Нет host UDP
listener, default route, NAT, firewall modifications или внешнего трафика.

В каждом run хешируются selected Docker identity/start/PID/running metadata,
filter rules IPv4/IPv6, host IPv6 routes, link names и named namespaces. После
cleanup сравниваются они и host IPv4 routes. Сохраняются только booleans, не
исходные правила или peer identifiers. Это проверка сохранения выбранного
состояния, не доказательство исправности существующих пользовательских VPN.

Два positive runs требуют handshake, ненулевых rx/tx, двустороннего ping и
отказа ping после удаления server peer. Два negative runs намеренно ошибаются
после setup и при существующем файле ключа. Требуются правильная стадия ошибки,
полная очистка и неизменное исходное окружение. Каждый run проверяется перед
следующим; при нарушении дальнейшие тесты и выпуск receipt прекращаются.

## Security/build evidence

Go toolchain scanner — 1.26.9, официальный archive SHA-256 проверен; scanner
`golang.org/x/vuln/cmd/govulncheck@v1.9.0`, BSD-3-Clause, отдельный admin tool.
Продуктовые Go binaries остаются прежними (Go 1.25.14); runtime pair не менялся.
Binary scan нашёл 0 symbol/package findings и 42 unique module advisory IDs.
DB timestamp и native scanner SBOM сохранены в
[audit receipt](../evidence/i-010-go-linux-audit.json).
Это symbol/package evidence для изолированного Linux staging test, а не
security clearance Android, native C/musl audit или полный release SBOM.
Binary mode имеет ограничения: [Go documentation](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck).
Systemd properties: [official documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec).

## Откат и DoD

Нормальное завершение и оба error paths удаляют только созданные runner
processes, namespaced links, exact socket paths и private temp directory.
SIGTERM проходит через cleanup; при зависании systemd ограничивает runtime и
убивает оставшуюся cgroup. SIGKILL/сбой ядра может обойти Python cleanup и
оставить named namespace/tmpfs directory: итоговый receipt тогда отсутствует,
дальнейшие тесты не запускать. Нельзя очищать по маске или трогать existing VPN.

Старый binary release при этом тесте не меняется. Проверка rollback относится к
test runtime; upgrade rollback на другую версию не заявляется, поскольку старой
версии до первого размещения не было. Это известная граница I-010, а не pass
несуществующего upgrade test.

Только после успешного live run и проверки нового датированного
`docs/evidence/i-010-staging-acceptance.json` можно отметить I-010 выполненным
в указанной границе. До этого статус — pending. I-011 остаётся незавершённым.

## Исправление первого live run: noexec `/run`

Первый live acceptance остановился на start_pinned_awg; cleanup и все selected
state comparisons прошли. Read-only SSH подтвердил `/run` tmpfs noexec на Ubuntu.
В WSL то же поведение воспроизведено добавлением NoExecPaths=/run к transient
service. Причина — первоначальный runner копировал executable bytes в `/run`.
Теперь public binaries отделены от key storage, а NoExecPaths=/run явно задан
и в staging task. Реальные local positive/setup-failure/key-file-failure/deadline
runs с этим запретом прошли. Это локальная регрессия, успешный VPS rerun ещё
не получен. [Минимальный failure receipt](../evidence/i-010-staging-failure-2026-10-19.json).

## Live evidence 2026-10-19

После noexec fix заключительный VPS run прошёл: `ok=48 changed=2 failed=0
unreachable=0`; [новый receipt](../evidence/i-010-staging-acceptance.json) проверен.
Все 4 runs подтвердили preservation/cleanup, два positive runs — реальный
протокол и peer removal. I-010 завершён в описанной границе; этот status не
переводит native SDK ADR-002/003 в Accepted и не утверждает public ingress policy.

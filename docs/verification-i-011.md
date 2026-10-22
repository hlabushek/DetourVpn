# I-011: Android native POC — текущий результат 2026-10-19

**Технический I-011 принят как native POC:** Pixel 8 Pro → staging, handshake,
egress, IPv6/DNS sample, network switch, disconnect и индивидуальный revoke
с прекращением трафика подтверждены. Границы приёмки — ADR-022; публичный выпуск
не разрешён. Актуальный дополнительный receipt —
`docs/evidence/i-011-revoke-staging.json`; предыдущий staging receipt и записи
ниже сохраняют историю неудачных попыток и не меняются задним числом.
Новая ветка `codex/i-011-android-poc`; исходное дерево было полностью untracked,
предыдущие файлы/инфраструктура сохранены. Коммита/публикации не выполнялось.

## Финальный повтор после исправления revoke

Direct UAPI revoke принят на VPS: peer удалён при active/ready движке, Pixel
остаётся native UP, но обе HTTPS IP-пробы перестают проходить. Повторный revoke
успешен. После полной потери физических сетей и их возврата VPN восстановил
свежий handshake/egress. Принудительный deep Doze дал IDLE, после unforce
handshake/egress сохранились; это не измерение естественного hardware suspend.

После stop узла обе пробы при native UP закрыты; после disconnect IPv4 и IPv6
снова доступны, адрес не staging. Сразу после первого отдельного revoke IPv6
проба ещё не проходила; успешный последующий повтор сохранён отдельно, причина
прежнего transient результата не установлена. Изменение appops deny само по
себе не отключило активный VPN и не засчитано как системный live revoke.

Оба новых запуска очищены с семью baseline checks. Телефонные test files и
instrumentation APK удалены, POC остановлен, deviceidle/battery simulation
сброшены, Wi-Fi/mobile включены, VPN permission denied. Временный sudo grant
отозван; повторный sudo -n отказал. Никаких секретов в receipt нет.

Проверки исправления: **174 passed, 166 database tests skipped** без test DB;
Ruff, mypy (60 files), format изменённых файлов и Ansible syntax passed.
Android код в этом исправлении не менялся; предыдущие build/lint/7 unit tests
относятся к тому же APK. До VPS исправление реально проверено локально с
private mount namespace и отдельно с handshake/peer removal blocks ping.
Ниже — исторический отчёт предыдущего прогона, его открытые пункты не являются
текущим статусом принятого POC. Release gates и непроверенные сценарии ADR-022
остаются открытыми. Следующая issue — I-012.

## Предыдущая живая приёмка 2026-10-19 (история)

Продолжение после этой приёмки: причина revoke воспроизведена в реальном
локальном private mount namespace; direct UAPI исправление прошло удаление и
повторный отзыв без изменения sandbox/capabilities. Отдельный локальный туннель
подтвердил handshake и прекращение ping после удаления peer. Обновлённые
проверки: 174 passed, 166 database skipped, Ruff/mypy/Ansible syntax passed.
Receipt: `evidence/i-011-revoke-local.json`. Новая VPS-приёмка ещё не выполнена;
ниже остаётся точный результат предыдущей установленной версии.

Проверка выполнена на физическом Pixel 8 Pro, Android API 37, и согласованном
staging 45.10.166.106. Машиночитаемые результаты и InvocationID сохранены в
`evidence/i-011-staging-window.json`; исторический v1 rollback failure сохранён
отдельно и не переобозначен как успешный.

- До VPN доступны IPv4 и IPv6, IPv4 не совпадает со staging. При native UP
  получен свежий handshake, HTTPS IPv4 совпадает со staging; IPv6-only проба
  не проходит. После нормального disconnect IPv4/IPv6 доступны, egress прежний.
- Wi-Fi → cellular подтверждён физическим underlying transport и пробой через
  VPN; Wi-Fi восстановлен. Screen off/on пройден, deep suspend/Doze не проверен.
- Три сгенерированных DNS-запроса дали только ожидаемые Cloudflare resolvers.
  Это выборочная проверка, общий DNS leak gate остаётся ограниченным.
- При остановке узла с native UP обе IP-пробы перестали проходить. Немедленное
  восстановление сети после последующего disconnect не подтверждено; новая
  сессия показала рабочую исходную сеть. Причина требует отдельной диагностики.
- Индивидуальный peer remove дважды вернул `i011_control_refused` (при UP и DOWN).
  Его успех не заявляется. Остановка всего endpoint не заменяет peer revoke.
- Реальное системное окно отказа VPN проверено: отказ не запускает native
  tunnel. Отзыв разрешения во время активного туннеля ещё не проверен.
- Forced-stop и фактический systemd deadline 600s завершились cleanup с семью
  совпавшими baseline checks. Deadline: 13:23:46–13:33:46 UTC; Result=timeout
  ожидаем, ExecStopPost=0. Test namespace отсутствует, контейнеры сохранены.
- Временный sudo grant отозван вручную через expire; последующий sudo -n
  отказал. Автоматическое истечение именно 45m grant не ожидалось и не измерено.
  Тестовые файлы и instrumentation APK удалены, POC остановлен. Wi-Fi/mobile
  data включены, VPN permission запрещён; основной POC APK остаётся установлен.

Test-only `PocNetworkTests` и `scripts/device_network_acceptance.py` передают
профиль через stdin/ADB в приватный временный файл приложения, удаляемый после
разбора. Private key не передаётся с телефона. Проверено отсутствие harness в
main debug/release DEX и наличие только в test APK. Новый test harness требует
API 33+, фактическая приёмка выполнена на API 37.

Финальная сборка debug/release/AAR/test APK прошла, lint: 0 errors и один прежний
warning о Gradle; 7 Android unit tests прошли. Ruff и mypy (60 файлов) прошли,
pytest: **165 passed, 166 skipped** без VPN_TEST_DATABASE_URL, OpenAPI --check
прошёл. Формат изменённых Python файлов проверен; глобальный format check имеет
прежнее замечание к `services/api/tests/test_migrations.py`, файл не менялся.

I-011 остаётся открытым: индивидуальный revoke, восстановление после потери
узла, deep suspend, активный permission revoke и release licence/SBOM gates.
I-012 не начат. Production rollout не выполнялся.

## Исходная локальная реализация (до сетевой приёмки)

- `apps/android`: исполняемый Java Android POC без Flutter/готового product UI.
  Native adapter v1 проверяет capabilities и фактическую core version.
- SDK/core остались pinned по ADR-002. Reviewed GoBackend-only patch исключает
  root components, Java logging, Go device logging/SIGUSR2 dumps и UAPI socket.
  Добавлены foreground notification, lifecycle serialization и socket protection
  checks. Native криптография/transport algorithms не изменялись.
- KeyPair создаёт upstream library на телефоне. Private key только в памяти
  процесса, не сохраняется/не передаётся серверу/clipboard/Intent/логам. Это
  ephemeral POC, не Keystore-backed persistence и не гарантия стирания JVM памяти.
- Profile parser запрещает imported private key, unknown parameters, несколько
  peers, app bypass, split routes, DNS hostnames, IPv6 profile и endpoint hostname.
  Numeric IPv4 default route; ожидаемое блокирование IPv6 требует live leak test.
- UI различает native UP, свежий handshake и ещё не проверенный egress. Отказ
  разрешения не создаёт tunnel. Медленный document provider не блокирует native
  disconnect queue. Backup/device transfer исключены, экран FLAG_SECURE.

## Фактические проверки

- Go `1.25.14`, NDK `26.1.10909125`, Gradle `8.13`, AGP `8.13.0`, Android SDK/
  build-tools `36/36.0.0`, JBR `21.0.5`. Go runtime CLOCK_BOOTTIME patch применён
  `--fuzz=0`: 8 files, без rejected hunks; offsets в двух файлах описаны выводом.
- Native arm64-v8a/x86_64 .so реально собраны. Повтор дал **одинаковые SHA-256**
  для обеих ABI; это результат повторной native сборки на этой машине, не
  доказательство независимой bit-for-bit сборки полного APK.
- Собраны debug APK, unsigned release APK и release AAR. Android lint: 0 errors,
  1 warning о более новой версии Gradle; pinned toolchain сознательно сохранён.
  7 unit tests прошли. Gradle locks/strict dependency verification прошли.
- APK signature v2 и `zipalign -P 16` проверены. Только `libwg-go.so` для двух
  ABI; root executables/classes не упакованы. Licences/root notices включены,
  полный per-file review/release SBOM остаются открыты.
- На физическом **Pixel 8 Pro, Android API 37, arm64-v8a** прошли native load/core
  version/down state, device-generated key/AWG parsing, negative profiles,
  disabled backup и protected non-exported service. Runner использует synthetic
  documentation endpoint только для parser tests, без service/network calls.
- Проверка системного отказа permission реально прошла: `permission_required=true`,
  окно `com.android.vpndialogs`, Back, `system_permission_denial_no_native_tunnel=true`,
  `stage=complete`, instrumentation code `-1`. Ранее выданное разрешение только
  этому POC сброшено через appops перед negative test. Private PIN не запрашивался.
- Общие Python gates: Ruff lint и mypy (57 files) — pass; pytest — **90 passed,
  166 skipped** (PostgreSQL не подключался; backend/БД не изменялись).
  Общий format check обнаружил **старый** дефект `test_migrations.py`, уже записанный
  в I-010. Вне scope не исправлялся; format check нового Python script проходит.

## Security scan: интерпретация и пределы

`govulncheck@v1.9.0`, DB timestamp 2026-10-08T22:31:09Z. Binary scan c-shared
дал большой набор module/package/symbol findings, включая не связанные в APK
OpenPGP/net/http. Их нельзя назвать доказанными уязвимостями используемого пути
или скрыть по exit=0: JSON mode всегда может вернуть 0 при findings.
Фактический `go list -deps -tags linux` и source reachability scan с **явными
корнями каждого JNI/CGO export** дали 0 symbol findings; package-level запись
GO-2026-6604 относится к Windows os.Root junction escape, Android export paths её
не вызывают. Module-level findings и исходный binary report сохранены в ignored
cache; минимальная сводка и hashes — в receipt. Это не formal audit, не проверка
всего NDK/C кода и не гарантия безопасности SDK. Отдельный release/security gate открыт.

Официальные ограничения scanner:
https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck#hdr-Limitations

## Что остаётся

Read-only SSH подтвердил свободный UDP 51892 и отсутствие конфликтов предложенных
root subnets; сервер **не менялся**. Конкретное предложение endpoint/NAT/rollback и
условия approval записаны в [runbook](runbooks/android-poc.md). Предыдущее I-010
разрешение касалось isolated tests без WAN ingress. До нового разрешения такой
endpoint не запускался. После него нужны отдельный bounded runner/preflight и
physical connect/egress/DNS/IPv6/network-switch/revoke проверки.

### Продолжение 2026-10-19: подготовка сетевого окна

Подготовлены `scripts/android_staging_window.py`, отдельный unit
`infra/ansible/files/project-vpn-i011.service`, 22 synthetic command/OS tests
и ADR-020. Fresh preflight читает host/Docker/container routes, firewall, UDP
listeners, pinned binary hashes и права файлов. Отдельные namespace/veth,
точные tagged firewall rules, private range isolation, ephemeral keys в tmpfs,
600s service deadline, cgroup kill и unconditional cleanup hook реализованы
в коде. Private key телефона серверу не передаётся. Есть отдельная peer-remove
команда, которая явно не выдаёт удаление записи за измеренный cutoff трафика.

Фактические локальные проверки: Ruff lint — pass; format двух новых Python
файлов — pass; mypy — pass, 58 source files. Полный pytest: **112 passed,
166 skipped** из-за отсутствия VPN_TEST_DATABASE_URL. OpenAPI check — pass,
контракт backend не менялся. Общий format check имеет прежний failure только
в `services/api/tests/test_migrations.py`, вне I-011.

`systemd-analyze verify --man=no` на локальном WSL systemd 259 — exit 0;
WSL DrvFS сообщил executable/world-writable metadata исходного unit. При
установке на staging требуются root:root/0644 и повторный verify. Это не
проверка Ubuntu systemd на VPS, firewall/NAT или фактического deadline.

**Удалённых команд/установки/сетевых изменений в этом продолжении не было.**
Preflight на staging, forced-stop/partial-failure/timer rollback, перенос профиля,
телефонный egress/DNS/IPv6/switch/revoke остаются NOT RUN. Следующее действие
требует явного разрешения WAN endpoint/NAT по AGENTS.md. Команды и ручные
изменения перечислены в runbook. I-011 остаётся частично выполненным, I-012 закрыт
зависимостью; готовность реального VPN не заявляется.

### WAN approval получен; локальный sudo необходим

Владелец разрешил предложенный bounded endpoint явным ответом «делай».
ADB подтвердил подключённый Pixel 8 Pro. POC выведен на экран; UI hierarchy
использовалась только для чтения public key в ignored локальный файл. Private
key, логи других приложений и скриншоты не считывались. Non-root SSH в staging
успешен; `sudo -n true` вернул `a password is required`. Сетевых изменений нет.

Добавлены `infra/ansible/staging-android-window.yml`,
`infra/ansible/tasks/android-window-case.yml` и
`scripts/run_android_staging_window.ps1`. Они устанавливают root-owned source/unit,
проверяют SHA-256, запускают fresh preflight, forced-stop и отдельный реальный
600s deadline test, требуют cleanup по InvocationID и baseline hashes, сохраняют
только non-secret evidence. После тестов always-stop оставляет endpoint выключенным.
До ввода sudo helper не запускался в режиме deployment.

Локальный Ansible 2.21.5 syntax check — pass. Реальный запуск с WAN flag=false
отказал на localhost до SSH: changed=0, failed=1 ожидаемо. Synthetic local
template smoke — ok=4, changed=0, failed=0, без сетевых/системных команд.
Эти проверки не заменяют staging runtime; новый live receipt пока отсутствует.
Следующий шаг — одна команда helper в терминале владельца с интерактивным sudo,
потом Android egress/leak/revoke проверки. Повторное WAN approval не требуется.

### Первый live запуск: startup failure, сетевой DoD открыт

Владелец выполнил helper с локальным sudo. Staging recap: ok=18, changed=5,
unreachable=0, failed=1. Задача ожидания phone.conf исчерпала 25 повторов;
always-stop завершился успешно. Последующий read-only SSH подтвердил
ActiveState=failed, Result=exit-code, ExecMainStatus=1 и ExecStopPost status=0.
Это не доказательство полного rollback: baseline/cleanup report пока не получен.
Сводка: `docs/evidence/i-011-staging-window-failure-2026-10-19.json`.

Причина startup failure ещё не установлена. Первоначальный runner скрывал
даже фиксированный этап сбоя; это затруднило диагностику. Исправлено локально:
fixed checkpoints и безопасная категория в JSON error (без exception text,
аргументов или command output), отдельный -Diagnose режим с read-only preflight
под теми же systemd constraints, немедленный отказ ожидания при stopped unit,
ready marker только после настройки интерфейса и его cleanup. Увеличения
capabilities/ослабления sandbox нет. Новый WAN запуск не выполнялся.

Проверки исправления: Ruff/mypy — pass, 26 synthetic tests — pass, Ansible
syntax-check helper -Diagnose — pass. Реальный sandbox diagnostic ожидает
локального sudo; нельзя называть startup причину исправленной до его результата.
Android/egress, deadline и полная очистка сети остаются не подтверждены.

### Container namespace fix и временный control grant

Владелец передал результат -Diagnose: sandbox_exit=1, stage=container_routes,
failure_code=check_rejected; обычный preflight до этого прошёл. Локальный реальный
WSL systemd probe с теми же ограниченными capabilities воспроизвёл EACCES=13
для /proc/PID/ns/net. Другой реальный probe с bind-mounted namespace handle
прочитал routes (exit=0, valid JSON); mounts/process были убраны в finally.

Исправлен runner: Docker NetworkSettings.SandboxKey, строгий root-owned путь,
проверки container ID/PID/start и namespace inode до/после чтения. Добавлены
negative/restart/path tests, обязательный sandbox preflight до любого endpoint.
Capabilities и mount sandbox не ослаблены. Повторный live probe на VPS ещё
не выполнялся, поэтому устранение конкретного startup failure там не заявляется.

По прямому запросу владельца об одном локальном sudo-вводе добавлен ADR-021:
45-минутная host/boot/UID/clock/code-bound lease, root-owned isolated controller
с фиксированными действиями, точное sudoers правило и expiry timer. Helper
не хранит sudo-пароль, не исполняет произвольный shell/обновления root кода,
передаёт только public device key в stdin и сохраняет секретный profile только
в специальном канале передачи. Тесты проверяют expiry/clock rollback/reboot,
чужой UID/host, hash drift, лишние аргументы, receipt filtering и отзыв в finally.

Фактические проверки: **152 passed, 166 skipped** (PostgreSQL URL не задан),
Ruff и strict mypy (59 files) — pass; Ansible -SyntaxCheck -GrantControl — pass;
локальный visudo -cf для точного правила — parsed OK. Live grant/автоматическое
удаление правила/исправленный staging preflight пока NOT RUN. Следующий шаг —
одна команда -GrantControl владельцем с локальным вводом sudo, далее Codex может
выполнять фиксированные операции без пароля в пределах lease. Привилегированный
код после выдачи grant не обновляется без нового локального sudo.

### Live control работает; forced-stop выявил дефект rollback

Без дополнительного пароля выполнены status, diagnose, start, status/receipt и
stop/receipt через fixed controller. Исправленный sandbox preflight на VPS прошёл
(sandbox_exit=0), endpoint достиг ready. InvocationID:
13f4088cbb724c3f99568acf53038ea5. Телефон не подключался.

После forced stop: cleanup_complete=false. Containers, links, routes4 и ipv6_filter
совпали; filter_nat, namespaces и routes6 — нет. Non-root read-only проверка
подтвердила ActiveState=failed, ControlGroup пуст и оставшийся vpn-i011 namespace.
IPv6 routes содержат уменьшающийся expires. Snapshot v1 ошибочно включал expires
и chain counters из iptables-save; ip netns pids внутри sandbox снова зависел от
proc access. Evidence: i-011-forced-stop-failure-2026-10-19.json.

Исправлено локально: snapshot schema v2 нормализует только chain counters и route
expires, сохраняя policy/gateway/metrics. Namespace cleanup проверяет завершение
служебного cgroup, без ptrace к посторонним процессам. Отдельный recover доступен
только после остановки, удаляет собственные tagged rules/names и проверяет PID
из standalone root context. Legacy v1 marker архивируется после удаления своих
ресурсов и сверки containers/links/routes4; историческая полная сверка остаётся
НЕ подтверждённой. v2 drift не игнорируется. GrantControl выполняет recovery
до preflight, без старта endpoint. Capabilities не расширены.

Фактические проверки: 161 passed, 166 skipped, Ruff/mypy и syntax-check — pass.
Локальный реальный systemd RuntimeMaxSec=2s/SIGKILL/ExecStopPost probe с прежними
capabilities дал owned_processes_gone=true; expected timeout exit=1. Это проверка
механизма на WSL, не полного rollback на VPS. Привилегированный source на стенде
ещё не обновлён: lease фиксирует старые hashes и запрещает update. Для применения
исправления нужен локальный sudo через -GrantControl. Deadline/Android/egress/
leak/revoke остаются открыты.

Hashes/машиночитаемые результаты: [receipt](evidence/i-011-native-build.json).
Воспроизведение: [runbook](runbooks/android-poc.md). Решение: ADR-019.
I-012 не начинался, работающий VPN этим результатом не заявляется.

---

## Исторический inventory от 2026-10-18, до реализации POC

Дата: 2026-10-18. Статус: **I-011 не реализован; BUILD/DEVICE/NETWORK NOT RUN**.

## Проверено локально

- Pinned AWG Android source `ff15093bf3856fea0947923474870b86efb682f0`
  теперь получен как полный локальный checkout в игнорируемой `.cache/i-011/`.
  Оба submodule checked out на gitlinks: `amneziawg-tools`
  `ee0f0a9aa34ff0a0da4b3433b9512781cfe02843` и `elf-cleaner`
  `7efc05090675ec6161b7def862728086a26c3b1f`. Рабочее дерево чистое.
- 16 исходных Git blobs сверены по SHA-256 с `docs/evidence/i-002-sources.json`:
  **16 совпадений, 0 расхождений**. Первичная сверка Windows working files дала
  16 расхождений из-за автоматического LF→CRLF при checkout; для дальнейшей
  сборки нужен Linux checkout с LF. Проверка Git blobs устранила неоднозначность
  источника, но не доказывает результат сборки.
- В локальном Android SDK есть platform 36 и build-tools 36.0.0. Android Studio
  предоставляет JBR 21.0.5; Java в PATH — 8.
- Установлен NDK 28.2.13676358. В выбранном upstream source указан NDK
  26.1.10909125, поэтому совпадение build inputs не подтверждено.
- В WSL не найдены Go, JDK и необходимые инструменты Android сборки; в Windows
  Go, Flutter и Gradle не найдены в PATH.
- `adb devices -l` не показал подключённого устройства. Команда запустила
  только локальный ADB daemon; тестов на устройстве не было.
- Upstream Android Makefile фиксирует Go 1.24.2, а embedded module указывает
  `go 1.25.0`; автоматическую загрузку toolchain и применение runtime patch
  нельзя считать проверенной или закреплённой сборкой.

## Что блокирует критерий готовности

I-011 зависит от I-010, но staging VPS пока отсутствует и узел не развёрнут.
Также нет проверенного server binary, собранного Android AAR/APK, подтверждённого
license/native dependency inventory и физического Android устройства.
Следовательно, нет настоящего connect/disconnect, измеренного нового egress IP,
DNS/IPv6 проверки или результата по смене сети. Никакие фиктивные подключения
или статусы `connected` не создавались.

## Следующая воспроизводимая последовательность внутри I-011

1. Использовать проверенную точную ревизию ADR-002 и gitlinks в Linux checkout
   с LF; проверить лицензии и выполнить контролируемый patch build inputs,
   описанный в `docs/compatibility-matrix.md`.
2. Собрать и проверить фактический Android артефакт: версии toolchain, resolved
   dependencies, native libraries, ABI, manifest, SHA-256, SBOM и release logging.
3. На явно одобренном staging host с тем же AWG core поднять test peer через
   отдельно проверенный key/config lifecycle. Не помещать private keys в repo,
   отчёт, логи или чат.
4. На физическом Android устройстве измерить connect/disconnect, egress IP,
   DNS/IPv6 и сценарии отказа из матрицы. Сохранить только обезличенные результаты
   и hashes сборок. До этого I-011 не отмечать выполненным.

Локальные команды этой проверки читали только установленный SDK и наличие
устройств. Ничего не развёрнуто на VPS и Android приложение не запускалось.

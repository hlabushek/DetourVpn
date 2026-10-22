# I-011: сборка и проверка Android native POC

Это локальная тестовая утилита Java + upstream GoBackend. Flutter, авторизация,
quota lease и backend provisioning сюда не входят. Ключ создаётся на телефоне
upstream KeyPair и исчезает с процессом; после process restart нужен новый peer.
Установка APK не доказывает VPN или отсутствие DNS/IPv6 утечек.

## Текущий результат и повтор сетевой проверки

2026-10-19 на Pixel 8 Pro (API 37) реально подтверждены handshake, staging IPv4
egress, Wi-Fi/cellular, выборочные IPv6/DNS проверки и обычный disconnect.
Forced-stop и фактический deadline 600s восстановили семь baseline checks.
Direct UAPI исправил индивидуальный revoke: peer удаляется при работающем
движке, трафик Pixel прекращается, повтор успешен. После потери узла disconnect
восстановил IPv4/IPv6. Техническая приёмка POC — ADR-022.
Полный актуальный итог: `../verification-i-011.md`
и `../evidence/i-011-revoke-staging.json`. Последующие разделы сохраняют также
историю подготовки; старые NOT RUN не описывают финальное состояние.

Сейчас endpoint остановлен, временный sudo grant отозван, test APK удалён,
POC остановлен. Для повторения нужен новый локальный `-GrantControl` по разделу
ниже; grant сам не запускает WAN endpoint. Пароль вводится только локально.
После сборки и установки debug/test APK запустить в первом терминале:

```powershell
adb shell am instrument -w -e network true org.example.vpnpoc.test/org.example.vpnpoc.PocDeviceTests
```

Harness требует API 33+, разблокированного телефона и системного VPN consent.
Во втором терминале из корня проекта дождаться `waiting_profile`:

```powershell
uv run --frozen python scripts/device_network_acceptance.py status
uv run --frozen python scripts/device_network_acceptance.py key
```

`key` передаёт только public key в остановленный endpoint. Затем через уже
проверенный staging SSH вызвать фиксированный контроллер `start` и `status`,
дождаться `ready=true`. Команды телефона:

```powershell
uv run --frozen python scripts/device_network_acceptance.py connect
uv run --frozen python scripts/device_network_acceptance.py dns
uv run --frozen python scripts/device_network_acceptance.py probe
uv run --frozen python scripts/device_network_acceptance.py disconnect
uv run --frozen python scripts/device_network_acceptance.py finish
```

Профиль передаётся напрямую из памяти процесса через stdin/ADB в app-private
временный файл и удаляется после разбора; private key остаётся на телефоне.
Не запускать действие `profile` вручную с выводом в терминал. Harness присутствует
только в test APK. `finish` отключает tunnel и удаляет собственные временные
файлы; после теста остановить endpoint, проверить receipt, вызвать `expire`,
force-stop POC и удалить `org.example.vpnpoc.test`. Успешное завершение
instrumentation само по себе не означает прохождение всей сетевой приёмки.

DNS sample использует три сгенерированных запроса и публичный протокол
[dnsleaktest v1.4](https://github.com/macvk/dnsleaktest/tree/v1.4) сервиса bash.ws;
сохраняются только booleans ожидаемых resolvers. Это не полная проверка DNS всех
приложений. Screen off/on не заменяет deep suspend. Ошибку peer revoke нельзя
заменять утверждением об успешном отзыве после остановки всего узла.

## Закреплённые входы

SDK commit `ff15093bf3856fea0947923474870b86efb682f0`, core
`v3.1.20260814`, Go `1.25.14`, NDK `26.1.10909125`, AGP `8.13.0`,
Gradle `8.13`, compile/target SDK `36`, build-tools `36.0.0`.
JDK фактической проверки: Android Studio JBR `21.0.5`. Java bytecode target 17.
NDK/Go/Gradle archives проверены; source patch — `apps/android/patches/go-only.patch`.
Wrapper JAR проверяется SHA-256 скриптом PowerShell, distribution — самим wrapper.

В ignored `.cache/i-011/awg-android` нужен Git checkout точной ревизии ADR-002.
Новые SDK terms не принимать автоматически. На этой машине уже имелись SDK
licence files; AGP проверил их при установке build-tools 35 в первом запуске.
Итоговая сборка явно выбирает уже установленные build-tools 36.0.0.

```powershell
uv run --frozen python scripts/prepare_android_poc.py
wsl -d kali-linux -- bash '/mnt/c/Users/JetGame/OneDrive/Документы/ChatGPT/Vpn (пока без названия)/scripts/build_android_native.sh'
powershell -ExecutionPolicy Bypass -File scripts/build_android_poc.ps1
```

Linux build script ожидает `/tmp/vpn-android-build/ndk.zip` из официального
`https://dl.google.com/android/repository/android-ndk-r26b-linux.zip` и
`/tmp/go1.25.14.linux-amd64.tar.gz` из `https://go.dev/dl/`.
Hashes заданы в script; NDK SHA-1 — metadata Google, дополнительный фактический
SHA-256 записан в receipt. На другом workspace Linux путь команды нужно заменить.
Go workspace и NDK только в Linux `/tmp`, Windows SDK/JDK остаются отдельно.
Для обновления patch подготовить новый ignored SDK directory и повторить проверки,
не обходить source drift guard. `prepared.diff` отражает также manifest overrides.

Проверки supply chain/read-only source reachability:

```powershell
wsl -d kali-linux -- bash '/mnt/c/Users/JetGame/OneDrive/Документы/ChatGPT/Vpn (пока без названия)/scripts/audit_android_native.sh'
```

Нужен уже установленный standalone `govulncheck@v1.9.0` из I-010 в
`/tmp/vpn-govuln-1.9.0/govulncheck`. Это не bundled runtime dependency.
Audit-only `vpn_audit` entry points никогда не входят в APK. JSON exit=0 не
означает отсутствие findings: нужно читать уровни module/package/symbol.
Source scan без корней JNI был бы недостаточным.

## Артефакты и телефон

- `apps/android/app/build/outputs/apk/debug/app-debug.apk` — локальный debug APK.
- `apps/android/app/build/outputs/apk/release/app-release-unsigned.apk` — **не подписан**,
  не предназначен для пользователей/публикации.
- `apps/android/tunnel/build/outputs/aar/tunnel-release.aar` — local spike SDK.
- `apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk` —
  только тестовый runner, synthetic config не отправляет трафик.

Через локальный ADB из Android SDK установить оба debug APK и выполнить:

```text
adb install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
adb install -r -t apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w org.example.vpnpoc.test/org.example.vpnpoc.PocDeviceTests
```

Для отказа permission: разблокировать телефон, повторить instrument с
`-e permission true`. Runner открывает **системное** окно VPN и нажимает Back;
разрешение не выдаётся, native tunnel не запускается. Если ранее разрешение
уже выдано этой утилите, тест отказывается; отозвать его вручную в VPN settings.
Instrumentation code `-1`, `stage=complete` и все predicates=true — pass.
Code `0` даже при process exit=0 означает failure.

Утилита не экспортирует private key и не принимает его из profile. Public key
доступен в её окне; для staging нужен только он, не private key. Profile выбирается
через Android file picker; никакого автоматического получения из внешнего API.
При active test VPN ожидается ongoing notification. Fresh handshake не считается
egress test. После disconnect/crash возможен обычный интернет — kill switch не заявлен.

## Предложение следующего сетевого теста — ещё не применено

На уже одобренном staging `45.10.166.106` предлагается отдельный endpoint:
UDP **51892**, peer subnet **10.203.71.0/30**, отдельный namespace/uplink subnet
**10.203.72.0/30**, один ephemeral phone public key, максимальное окно **10 минут**.
Read-only SSH 2026-10-19 подтвердил, что 51892 не слушается, root routes не
пересекают эти подсети и IPv4 forwarding уже включён. Existing container-internal
routes/firewall нужно повторно проверить с scoped sudo перед применением.

Ожидаемый reviewable diff: свой namespace/veth, единственный AWG process/peer,
точечный UDP DNAT/FORWARD и source NAT к `ens3`; никаких flush, container edits,
default route/sysctl changes или остановки existing VPN. Отдельно блокировать
доступ peer к host/container/private/link-local ranges. Keys только в private tmpfs,
никаких raw configs, DNS/URL history или payload в отчёте. Rollback удаляет только
созданные по точному имени ресурсы/правила, проверяет исходные snapshots.

**Runner теперь подготовлен локально, на VPS не установлен и не запускался.**
Файлы: `scripts/android_staging_window.py`, `infra/ansible/files/project-vpn-i011.service`;
граница/допущения — ADR-020. Предыдущее I-010 approval покрывало namespaces без
внешнего порта/NAT. AGENTS.md требует явного разрешения для внешних side effects.
Sudo-пароль вводится владельцем локально, не в чате. До успешных physical egress,
DNS/IPv6/switch/revoke tests I-011 не закрывать и I-012 не объявлять готовым.

## Подготовленный runner: воспроизведение и ручные изменения

### Автоматизация после разрешения владельца 2026-10-19

#### Один локальный ввод sudo для автономных проверок

По отдельному запросу владельца добавлен ограниченный control grant (ADR-021).
Из любого каталога PowerShell выполнить одну команду:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\JetGame\OneDrive\Документы\ChatGPT\Vpn (пока без названия)\scripts\run_android_staging_window.ps1" -GrantControl
```

На `BECOME password` ввести sudo-пароль **только локально**. Helper не сохраняет
пароль; Ansible устанавливает узкое NOPASSWD-правило только для root-owned
`/opt/project-vpn/acceptance/i011_control.py`. Lease действует максимум 45 минут,
привязана к vpn-deploy UID, staging IPv4, boot ID, wall/monotonic clock и SHA-256
кода/unit. `-GrantControl` запускает только read-only sandbox preflight, без WAN
endpoint. После установки Codex использует non-root SSH с существующим ключом
и `sudo -n` **только** для этого helper, без дополнительных sudo-паролей.

Доступные действия: preflight/diagnose/start/status/stop/revoke/recover/key/profile/receipt/expire.
Выполнение произвольных команд и обновление root-кода через helper запрещены.
Любое изменение root source/unit инвалидирует lease и потребует локального
подтверждения новой версии через sudo. Это ограничение намеренное.
Private key телефона не нужен серверу. У POC нет отдельного пароля профиля.
Операция profile — исключительно закрытый канал в память клиента: её stdout
никогда не выводить в чат/tool output/log. Receipt выводит только безопасные поля.

Через 45 минут timer останавливает test service и удаляет собственный sudoers
grant/lease; каждый test service отдельно ограничен 600s. До live результата
автоматическое удаление grant не считается проверенным. Досрочный отзыв:

```text
sudo -n /opt/project-vpn/acceptance/i011_control.py expire
```

Эта команда предназначена только для локального терминала одобренного staging
пользователя. Другие sudoers правила не затрагиваются. При reboot tmpfs lease
исчезает и helper отказывает; если sudoers-файл сохранился, удалить именно его
через обычный локальный sudo. Повторное продление автоматически не выполняется.

WAN approval получен. Телефон Pixel 8 Pro виден через ADB, из окна POC считан
только публичный ключ в ignored `.cache/i-011/device-public-key`. Private key
не считывался. SSH под vpn-deploy работает, `sudo -n true` отказал с
`a password is required`. До локального sudo сетевые изменения не выполнялись.

В PowerShell из корня проекта выполнить **одну команду**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_android_staging_window.ps1
```

На `BECOME password` ввести sudo-пароль vpn-deploy локально. В чат пароль не
передавать. Команда займёт около 11–14 минут: установка проверенного кода/unit,
fresh preflight, запуск/принудительный stop, затем отдельный максимум 600s прогон
до deadline и очистки. В конце endpoint остановлен; systemd unit не enabled.
Два действующих контейнера и current release не меняются. Public key и код
останутся для следующей Android проверки, private runtime configs удаляет cleanup.
Playbook оборачивает тесты в always-stop; успешная приёмка требует совпадения
семи baseline hashes и cleanup из **текущего** systemd InvocationID.

Если recap содержит failed/unreachable, остановиться: передать только task name,
фиксированный error/recap, без `-vvv`, configs, ключей и паролей. В случае сбоя
связи полагаться только на измеренную позже server deadline/cleanup, не считать
успехом отсутствие связи. Evidence `docs/evidence/i-011-staging-window.json`
создаётся только после двух успешных live проверок. Его отсутствие — не приёмка.
Ни команда helper, ни evidence не подтверждают Android egress/DNS/IPv6.

Проверка helper без SSH: добавить `-SyntaxCheck`.
Если профиль не появился и основной процесс завершился, не повторять WAN-тест
вслепую. В helper добавлен **диагностический режим без запуска endpoint**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\JetGame\OneDrive\Документы\ChatGPT\Vpn (пока без названия)\scripts\run_android_staging_window.ps1" -Diagnose
```

Он обновит root-owned диагностический код/unit и выполнит read-only preflight
в отдельном systemd unit с теми же mount/capability ограничениями. Диагностический
unit ограничен 45s, не вызывает serve, не создаёт namespace/peer/NAT и не генерирует
ключи. Показать только `Display the fixed preflight diagnostic result` и recap.
В ошибке доступны `stage` и категория, exception text/stdout/stderr внешних
утилит не выводятся. Не расширять capabilities и не отключать sandbox по догадке.

Startup failure локализован в container_routes. Исправление использует root-owned
Docker SandboxKey bind mount вместо /proc/PID/ns/net с ptrace access check;
ID/PID/start/path/inode проверяются до/после чтения. CAP_SYS_PTRACE не добавляется,
проверка пересечений подсетей остаётся обязательной. Новый read-only sandbox
preflight теперь обязателен и перед обычным запуском тестов. Локальный реальный
systemd probe подтвердил EACCES для proc и успешное чтение через bind handle;
это ещё не результат исправленного probe на VPS.

Ниже — описание шагов, которые выполняет playbook, и ручного восстановления;
не выполнять их параллельно с ним.

Локальные проверки без SSH/сети:

```powershell
uv run --frozen ruff check scripts/android_staging_window.py infra/ansible/tests/test_android_staging_window.py
uv run --frozen ruff format --check scripts/android_staging_window.py infra/ansible/tests/test_android_staging_window.py
uv run --frozen mypy
uv run --frozen pytest infra/ansible/tests/test_android_staging_window.py -q
```

Следующие шаги покрыты полученным разрешением WAN-теста и выполняются
через одобренного non-root staging пользователя с локальным интерактивным sudo.
Production/root SSH credentials не нужны. Не выполнять приведённые команды на
другом сервере. Никаких install/start/SSH в текущей локальной проверке не было.

1. Передать проверенные `android_staging_window.py` и **текущий**
   `awg_local_smoke.py` в `/opt/project-vpn/acceptance/`, owner root:root, mode 0755.
   Передать unit в `/etc/systemd/system/project-vpn-i011.service`, root:root, 0644.
   Все родительские каталоги должны быть root-owned без group/world write.
   Сверить SHA-256 переданного кода с локальными файлами, не менять `/opt/project-vpn/current`.
2. Создать `/run/project-vpn-i011` и `/run/project-vpn-i011-input`, root:root, 0700.
   Записать **только публичный** ключ текущего процесса Android POC в
   `/run/project-vpn-i011-input/public-key`, root:root, 0600. Ключ не помещать
   в shell history/аргументы: передать файл приватным каналом. Private key телефона
   не запрашивается. Каждый process restart POC меняет public key.
3. На staging выполнить по одной команде:

```text
sudo systemd-analyze verify /etc/systemd/system/project-vpn-i011.service
sudo python3 /opt/project-vpn/acceptance/android_staging_window.py preflight
sudo systemctl daemon-reload
sudo systemctl start project-vpn-i011.service
```

`start` не означает готовность: ждать фиксированного `endpoint_ready=true` в
`sudo journalctl -u project-vpn-i011.service --since '2 minutes ago' --no-pager`.
Runner не выводит configs/keys, raw external errors подавлены. Не читать содержимое
`phone.conf` через cat, journal, чат или скриншоты. Ошибка preflight запрещает
продолжение; не обходить guard и не выбирать другие подсети автоматически.
Автоматизация ждёт отдельный root-only `ready.json`, записанный после настройки
интерфейса, и сразу отказывает при inactive/failed unit. Существование phone.conf
само по себе больше не считается готовностью; ready marker удаляется cleanup.

4. **Первый запуск — проверка аварийной остановки, без подключения телефона.**
   Выполнить `sudo systemctl stop project-vpn-i011.service`, получить
   `cleanup_complete=true` и все `restored=true`. Затем проверить отдельный
   прогон до deadline; `Result=timeout` ожидаем, но cleanup должен успешно пройти.
   Сбой/drift требует остановки и ручного разбора; повторная очистка разрешена
   только после подтверждения, что unit stopped и AWG process отсутствует:

```text
sudo python3 /opt/project-vpn/acceptance/android_staging_window.py cleanup
```

   При ошибке marker сохраняется; не удалять его вручную, чтобы обойти preflight.
   Имена/точные rules видны в runner. Не использовать flush, restore snapshot,
   удаление чужих PID или restart существующих контейнеров. Private tmpfs files
   удаляются cleanup; удалить input public-key и пустые runtime directories вручную
   после всей приёмки. Unit не enable-ить; удалить установленный unit/код после
   завершения I-011 и выполнить daemon-reload, сохранив I-010 smoke при необходимости.
5. Для Android запустить новое десятиминутное окно. Root-only
   `/run/project-vpn-i011/phone.conf` содержит shared HeaderProtectionKey и public
   server key, **не private device key**. Владелец переносит его по отдельно
   проверенному приватному каналу в Android file picker; копии вне tmpfs удалить
   сразу после теста. Не ослаблять права файла ради передачи. После импорта:
   permission → connect → fresh handshake → независимая проверка egress,
   DNS/IPv6 и network switch. Для egress ожидается staging IPv4; это нужно измерить.
   DNS resolver теста 1.1.1.1 выбран явно в ADR-020, production policy не определена.
6. Пока окно ещё активно, выполнить
   `sudo python3 /opt/project-vpn/acceptance/android_staging_window.py revoke`.
   `peer_removed=true` проверяет только список peers. На телефоне отдельно
   проверить прекращение трафика, затем disconnect и stop unit. Если десяти минут
   не хватило, новый public key/profile и новый прогон, без продления lease.

В отчёт сохранять только результаты/hashes, без ключей, raw config, DNS/URL history.
До live failure-cleanup, timer, network и revoke проверок runner — локально
проверенная подготовка, а не прошедшее deployment acceptance.

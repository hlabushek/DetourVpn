# Flutter Android client — I-013

Scope и ограничения: ADR-025; acceptance: `docs/issues/I-013.md`.
Установленный application ID beta-клиента — `com.detourvpn.client`; старый I-011
harness не заменяется. Внутренний Android namespace остаётся `org.example.vpnclient`.
Нельзя одновременно держать другой VPN и измерять этот тестовый туннель.

## Локальная сборка

Нужны Windows/PowerShell, JDK Android Studio, Android SDK 36/build-tools 36,
upstream GoBackend-only native libraries из runbook `android-poc.md`.
Flutter 3.47.7 (BSD-3-Clause), revision/archive SHA256 закреплены в
`apps/client/toolchain.json`; Dart dependencies — `pubspec.lock` (hashes).
App и tunnel dependency graphs нового Flutter host фиксируются в
`android/app/gradle.lockfile` и `android/tunnel-gradle.lockfile`; самостоятельный
I-011 lockfile не изменяется Flutter-сборкой.
Flutter engine ABI artifacts закреплены engine hash из SDK, а не требованием
присутствия всех ABI в lockfile: физический Pixel собирается только для arm64.
Обычный debug также включает штатный native plugin `integration_test` из SDK
(dev dependency); его зависимости фиксируются в debug lock configurations.
Release исключает dev plugin; это дополнительно проверяется APK audit.
Gradle 8.14.3 (Apache-2.0) и Kotlin build plugin 2.2.20 (Apache-2.0) фиксированы.
AGP 8.13.0 выбран для совместимости с проверенным native module; Flutter выдаёт
предупреждения о будущем прекращении поддержки этих версий. Проверка требований
SDK не отключается; переход на AGP 9 — отдельное совместимое обновление tooling.

```powershell
.\scripts\install_flutter.ps1
.\scripts\build_flutter_client.ps1
```

Скрипт создаёт свежий snapshot разрешённых исходников в
`%LOCALAPPDATA%\ProjectVpn\i013-builds\<random-id>`: client, native app sources,
tunnel build/lockfile и подготовленный SDK. Private API/staging configuration,
local.properties и старые build trees не копируются. Проверки работают там;
обычные APK и reports возвращаются в исходный `apps/client/build/app`.
Это устраняет падение Dart LSP на кириллице и отказ Dart stat() на cloud reparse
points OneDrive. Исходники редактировать только в основном checkout; локальный
snapshot остаётся для диагностики, не является рабочим исходным проектом.
Никакие ключи или сессии для этой сборки не нужны. NDK 27.0.12077973 может быть
установлен Android Gradle tooling, если отсутствует. Никакие PATH/system
environment настройки не сохраняются. Сборка устанавливает зависимости локально,
запускает analyze/tests и Android unit/lint/release checks. Unsigned release APK
не является публичным выпуском; signing/publication этим этапом не выполняются.
Проверка 2026-10-29 показала, что release-вариант не был подписан; последний
owner beta install был debug APK. Gradle теперь поддерживает отдельный beta key,
поданный только через переменные окружения процесса сборки. Если заданы не все
четыре переменные, release signing не конфигурируется; при частичном наборе
Gradle отказывает с перечислением только отсутствующих имён переменных. Штатный
build script требует существующий keystore по абсолютному пути вне checkout.
Gradle запрещает включить release signing, пока application ID равен
временному placeholder `org.example.vpnclient`.
Конфигурация и проверка описаны в ADR-066 и
`docs/evidence/i-030-beta-signing-config-20261029.json`.

Перед сборкой signed beta APK владелец должен вне репозитория создать/выбрать
beta signing key, организовать закрытое хранение и резервное восстановление
ключа. `com.detourvpn.client` — постоянный beta ID;
его смена после первой раздачи нарушит совместимость обновлений. Не отправлять
ключ, пароли или переменные с ними в чат и не записывать их в репозиторий.
Перед передачей APK тестерам требуется проверить подпись, application ID,
версию, SHA-256 и непрерывность обновления с предыдущей beta-версии.

`scripts/build_beta_apk.ps1` выполняет release build с этим ключом. Для beta API,
настроенного на window tunnel catalog, добавляйте `-WindowTunnels`, чтобы клиент
обращался к соответствующему API и native tunnel controller. При первом
запуске скрипт предлагает создать JKS в `%LOCALAPPDATA%\ProjectVpn\beta-signing`
и продолжает только после локального ввода `CREATE`. `keytool` запрашивает
пароль JKS и пароль ключа в терминале; путь вводить не надо. Выбрать пароли не
короче 16 символов и сохранить у владельца. При запросе пароля ключа Enter
повторно использует пароль хранилища. Перед build скрипт повторно запрашивает
оба пароля скрытыми полями, временно передаёт их только процессу сборки и
очищает переменные после проверки APK. Установки на устройство и передачи APK
скрипт не выполняет.
Ключ и пароли нужно отдельно сохранить в контролируемом владельцем резерве;
локальный каталог сам по себе не является backup.

### Резервная копия signing JKS на BitLocker USB

Для `GERFFINSUSB (E:)` сначала завершить BitLocker To Go и убедиться, что диск
разблокирован. Сохранить пароль разблокировки и recovery key BitLocker в
менеджере паролей; оба пароля JKS хранить там же как отдельные секреты. Не
класть recovery key только на зашифрованный USB и не передавать значения в чат.

Из PowerShell, запущенного с повышенными правами под той же учётной записью
JetGame, выполнить:

```powershell
.\scripts\backup_beta_signing_keystore.ps1 -DriveLetter E:
```

Скрипт сверяет метку и съёмный тип диска, требует полностью завершённое
BitLocker-шифрование и включённую защиту, отказывается перезаписывать готовую
или оставшуюся частичную копию и сравнивает SHA-256 исходного JKS с копией.
Успешный вывод `code=beta_signing_keystore_backup_verified` подтверждает точную
копию на зашифрованном диске; он не подтверждает, что recovery key BitLocker
или пароли JKS сохранены. Хранить сам JKS и любые резервные ключи вне checkout.

### Проверка восстановления signing JKS

Перед проверкой локально убедиться в менеджере паролей, что там сохранены пароль
разблокировки и recovery key BitLocker, а также оба пароля JKS. Значения никуда не
копировать и не отправлять. Разблокировать `GERFFINSUSB (E:)` и выполнить из
обычного PowerShell под владельцем; тестовая копия создаётся в уже защищённом
каталоге `%LOCALAPPDATA%\ProjectVpn\beta-signing` вне checkout:

```powershell
$restoreCopy = Join-Path $env:LOCALAPPDATA ('ProjectVpn\beta-signing\detourvpn-beta-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.jks')
$backup = 'E:\DetourVpn-Beta-Backup\detourvpn-beta-signing.jks'
if (Test-Path -LiteralPath $restoreCopy) { throw 'Restore-check destination already exists.' }
Copy-Item -LiteralPath $backup -Destination $restoreCopy
$sourceHash = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash
$restoreHash = (Get-FileHash -LiteralPath $restoreCopy -Algorithm SHA256).Hash
if ($sourceHash -ne $restoreHash) { throw 'Restore-copy SHA-256 mismatch.' }
'restore_copy_sha256=verified'
.\scripts\build_beta_apk.ps1 -KeystorePath $restoreCopy -ApiOrigin 'https://193.68.88.239'
```

Сборка запросит оба пароля JKS скрыто и подпишет локальный beta APK восстановленной
копией. До очистки тестовой копии сверить fingerprint в новом receipt с предыдущим
beta receipt. После успешной сверки удалить только файл из `$restoreCopy`; исходный
JKS и резерв на USB не трогать. Если сборка или сверка не удалась, сохранить
тестовую копию до выяснения причины. Эта проверка не заменяет естественный запуск
ежедневного backup task и не доказывает хранение BitLocker recovery key в менеджере
паролей.

```powershell
.\scripts\build_beta_apk.ps1 -ApiOrigin 'https://YOUR_APPROVED_API_HOST'
```

Для beta сборки `-ApiOrigin` обязателен. Скрипт принимает только HTTPS origin
без credentials, пути, query или fragment и передаёт его в debug и release
сборки как `VPN_API_ORIGIN`.

Для локальной проверки уже подписанного APK создать отдельный receipt:

```powershell
uv run --frozen python scripts/verify_beta_apk.py `
  --apk apps/client/build/app/outputs/flutter-apk/app-release.apk `
  --expected-application-id com.detourvpn.client `
  --output docs/evidence/i-030-beta-apk-20261029.json `
  --sdk-root "$env:LOCALAPPDATA\Android\Sdk" `
  --java-home 'C:\Program Files\Android\Android Studio\jbr'
```

Владельцем выбран `com.detourvpn.client`. Для каждого следующего receipt укажите
новый путь, чтобы не перезаписывать прежнее evidence. Receipt содержит ID,
версию, SHA-256 APK и fingerprint сертификата, но не путь к APK. Скрипт
использует `aapt` и `apksigner` из Android
SDK/JDK без вызова Windows batch wrapper, не устанавливает APK и оставляет `update_install: not_verified` и
`distribution: not_accepted`. Физическую установку поверх предыдущей beta-версии
и фактическую передачу тестерам надо принять отдельно.

Debug и release собираются последовательно через `flutter build apk` с явным
`--target lib/main.dart`. Между ними Gradle выполняет только debug unit/lint.
Не объединять debug и `:app:assembleRelease` в одном прямом Gradle-вызове:
Flutter должен заново сгенерировать native plugin registrant для release,
исключив `integration_test`. Иначе javac получает ссылку на отсутствующий dev plugin.
В закреплённом SDK `--no-pub` также пропускает эту регенерацию, поэтому release
запускается без него после `pub get --enforce-lockfile`; перед экспортом скрипт
проверяет, что SHA256 `pubspec.lock` не изменился.
`-ApiOrigin`, если задан, применяется к обеим сборкам.

APK: `apps/client/build/app/outputs/flutter-apk/app-debug.apk`.
Для установки обычного debug APK после сборки и открытия на единственном
подключённом Android-телефоне:

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r apps/client/build/app/outputs/flutter-apk/app-debug.apk
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" shell am start -W -n com.detourvpn.client/org.example.vpnpoc.ClientActivity
```

Без API origin приложение честно показывает setup screen. Для настоящего API:

```powershell
.\scripts\build_flutter_client.ps1 -ApiOrigin 'https://YOUR_APPROVED_API_HOST'
```

Не включать токены/credentials в origin или dart-define. Release разрешает только
HTTPS с системной проверкой сертификатов. Локальный debug-only transport:
`http://127.0.0.1:18766` через ADB reverse; plaintext remote hosts запрещены.
Исключение Android network security config существует только в debug source set.

## Диагностика по согласию

При ошибке подключения кнопка «Поделиться диагностикой» открывает предпросмотр.
Проверьте код и этап; отмена закрывает диалог без вызова Android share API.
После подтверждения Android показывает системный Sharesheet, а текст попадёт
только в выбранное пользователем приложение. Отчёт содержит фиксированный код
ошибки и этап; неизвестные значения заменяются на `unknown_error`/`unknown`.
Аккаунт, ключи, адреса, список приложений, DNS, история трафика и содержимое
туннеля не включаются. Автоматической отправки и crash reporting нет.

Постоянный контакт поддержки пока не настроен. Тестер выбирает получателя
самостоятельно; до отправки следует сверить предпросмотр и не дополнять его
секретами, профилем, OTP или пользовательским трафиком. Физическая проверка
Sharesheet и инструкции на beta APK остаётся открытой.

## Проверки телефона без внешнего endpoint

Подключить и разблокировать ровно один телефон, разрешить USB debugging.
Для permission cancellation test приложение должно ещё не иметь VPN approval:

```powershell
.\scripts\build_flutter_client.ps1 -DeviceChecks
.\scripts\run_i013_android.ps1
```

Первый тест использует настоящий upstream core/key/channel: native DOWN,
invalid profile/proof/TTL rejection, отмена незавершённого системного permission.
Туннель к внешнему endpoint не создаётся. Второй тест использует disposable real
PostgreSQL + loopback FastAPI + настоящий Flutter UI/native. Telegram sender
**TEST FAKE ONLY**, код передаётся через test-only loopback route; пользовательский
Telegram и production API не затрагиваются. Native permission заранее разрешается
через app-op только для этого тестового package, после прогона возвращается default.
Проверяются login UI, device/key proof, admission closed и отсутствие ложного
connected; API tunnel intent удаляется, приложение останавливается, reverse
удаляется, API/PostgreSQL останавливаются и временная БД удаляется.
Санитизированный receipt: `docs/evidence/i-013-local-android.json`.

Integration entrypoints находятся только в integration_test, test server routes
только в pytest-модуле. Они не включаются в обычный main.dart/release API.
После integration test пересобрать обычный APK: `build_flutter_client.ps1`.
Не распространять APK, собранный с test entrypoint. Raw Flutter failure output
хранится только в памяти runner и не показывается, потому что widget diagnostics
могут содержать код входа. Receipt содержит только фиксированные flags/stage.

## Новый bounded staging прогон — отдельное разрешение

I-013 уже технически принят 2026-10-21: собственный physical Flutter receipt
`docs/evidence/i-013-staging.json`, `passed=true`; 1 passed / 391 deselected.
Ниже — процедура воспроизведения нового прогона. Для просмотра интерфейса
повторять staging не требуется. Реальная Telegram delivery, DNS/IPv6 и смена
Wi-Fi/mobile для этого APK не проверены; release gates остаются открыты.

Требует явного согласия на один ограниченный прогон на `45.10.166.106` в рамках
неизменённого ADR-024 controller. Существующие VPN/containers/routes не менять.
Endpoint и agent до 600s, expiring grant до 45m; 3 независимых client lease до
60s/1 MiB, без продления. Production operations не входят в разрешение.

После согласия пользователь запускает локально (пароль только в Ansible prompt):

```powershell
.\scripts\run_i013_android.ps1 -Staging
```

Для уже выданного свежего grant, после проверки причины предыдущего сбоя:
`-Staging -UseExistingGrant`. Не повторять провалившийся прогон вслепую.
Пароль, SSH key, profiles и access/refresh/code не отправлять в чат.

Runner использует проверенный I-012 controller без нового remote shell surface.
До запроса sudo/grant выполняется локальная компиляция точной integration test
точки входа и ABI подключённого телефона; при ошибке VPS не затрагивается.
API остаётся loopback, агент получает его через SSH reverse, Android — через ADB
reverse. Header protection profile хранится вне checkout под user-only ACL.
Telegram delivery остаётся TEST FAKE; real auth/API/node/Android/egress — actual.
Сценарии: UI login → create → node ACK → native handshake → staging egress;
background/foreground; Disconnect → revoked/settled → восстановление сети;
server revoke → UI/native DOWN; новый lease → expiry → local DOWN/settlement.
Receipt `docs/evidence/i-013-staging.json` не заменяет raw assumptions:
`passed=true` допустим только после всех facts и cleanup.

Cleanup: remote expire/семь baseline checks + grant removal; SSH transport close;
stop phone/reset own app-op/remove reverse; secret profile/key removal; stop local
API/PostgreSQL. Runner не оставляет действующий peer или постоянно работающий API.
Неуспешный cleanup должен быть устранён до нового staging прогона.

## Ручной acceptance checklist

- Отказ в Android VPN permission: нет connected и реального native UP.
- Connect/Disconnect меняет системный VPN; native UP без handshake не connected.
- Wi-Fi/mobile loss и возврат: reconnecting, без автоматического нового peer;
  состояние пересчитывается при возврате экрана. До согласованного теста не
  переключать пользовательские radios автоматически.
- Проверить реальный IPv4/IPv6/DNS и возврат обычной сети; свежий handshake сам
  по себе не доказывает отсутствие утечек. I-011 sample не заменяет новый APK test.
- Theme/font scaling/screen reader; token/key/profile не показываются/не логируются.
- При API failure cached quota имеет отметку о давности; bytes всегда integer.

Known limitations: ephemeral key/session, ручной новый вход после process death,
device-limit cleanup через существующий device API/account tooling; один staging
endpoint; нет auto renewal/region selection/full history. После утраты сессии
можно войти снова для завершения pending revoke; intent остаётся только в RAM.
При смене аккаунта/смерти процесса server kernel lease остаётся последним gate;
клиент не обещает подтверждённый remote revoke без node ACK.
Public release требует licence/supply-chain/SBOM/security/privacy gates.

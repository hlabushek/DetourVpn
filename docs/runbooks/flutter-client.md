# Flutter Android client — I-013

Scope и ограничения: ADR-025; acceptance: `docs/issues/I-013.md`.
Новый package `org.example.vpnclient`, старый I-011 harness не заменяется.
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
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" shell am start -W -n org.example.vpnclient/org.example.vpnpoc.ClientActivity
```

Без API origin приложение честно показывает setup screen. Для настоящего API:

```powershell
.\scripts\build_flutter_client.ps1 -ApiOrigin 'https://YOUR_APPROVED_API_HOST'
```

Не включать токены/credentials в origin или dart-define. Release разрешает только
HTTPS с системной проверкой сертификатов. Локальный debug-only transport:
`http://127.0.0.1:18766` через ADB reverse; plaintext remote hosts запрещены.
Исключение Android network security config существует только в debug source set.

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

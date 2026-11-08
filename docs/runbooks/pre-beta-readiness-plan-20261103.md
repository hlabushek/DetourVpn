# Предрелизная подготовка закрытой беты

Дата: 2026-11-03. Документ объединяет открытые проверки I-027, owner VPN/revoke,
I-028/I-029 и Phase 6 I-030. Это план и снимок доказательств, а не изменение
критериев, статусов issue или разрешение на production-действия.

## Краткий порядок

1. Дождаться естественного запуска ежедневного backup в 07:00 Europe/Moscow и
   восстановить именно созданный им архив в disposable PostgreSQL.
2. До подключения телефона закончить доступные локальные проверки, проверить
   подписи/версии зависимостей и подготовить короткий owner VPN/revoke прогон.
3. При одном физическом визите сначала выполнить owner connect → минимальный
   HTTPS egress probe → disconnect → подтверждённый сервером `closed` → сверку
   учёта. Включить split mode «Через VPN только выбранные», выбрав Chrome.
4. Не повторять прошедший I-028 source-bound Egress run. Отдельно решить общий
   release DNS/IPv6 audit: ADR-072 снимает его только с личного I-028 checkpoint,
   но не отменяет требования `TECHNICAL_SPEC.md` к проверке на реальной сети.
5. Закрыть Phase 6: полный security/SBOM/licence gate, разрешённый staging load и
   usage-lag, SLO/rotation/backup измерения, Android memory/battery и расчёт цены.
6. Только после evidence-review решить готовность беты и выполнить выбранную
   владельцем публикацию подписанного APK в приватный канал.

## Снимок имеющихся доказательств

| Область | Что подтверждено | Что остаётся |
|---|---|---|
| I-027 backup | Ручной запуск 2026-11-02 завершился кодом 0; архив аутентифицирован и восстановлен в disposable PostgreSQL 18.6: 42 manifests и обе ledger accounts сверены. | На проверке 2026-11-03 задача была `Ready`, прошлый запуск — ручной 2026-11-02 15:04, следующий — естественный trigger 2026-11-03 07:00. Этот trigger ещё не наблюдался. `StartWhenAvailable`/catch-up тоже не доказан. См. `docs/issues/I-027.md` и `docs/evidence/i-027-restore-drill-20261102.json`. |
| Owner VPN/revoke | Pixel 8 Pro и подписанный beta APK ранее были проверены read-only helper. Владелец сообщил handshake при попытке 2026-11-02. | Тот прогон не подтвердил IPv4 egress: два echo probe не ответили; списано 11,930,289 bytes, attribution к probe отсутствует. После Disconnect локальный VPN выключился, но состояние `PeerConnection=closed` отдельно не проверялось. Не повторять full-tunnel попытку. См. `docs/evidence/i-030-owner-window-egress-failed-20261102T204947Z.json`. |
| I-028 Egress | Source-bound physical run 2026-11-02 прошёл шесть ограниченных фактов; settlement 19,723,210 bytes/20 receipts, ноль reserved/unknown/open bytes и ledger violations; cleanup прошёл. Все 11 source hashes в receipt совпали с текущими файлами. | Receipt остаётся `diagnostic_only`; это не device-wide leak-free или release-privacy заключение. Included IPv6 evidence для личного checkpoint основано на принятом ADR-071 same-port/AP capture от 2026-10-31. Широкий capture снят с личного checkpoint по ADR-072, но общий release audit не принят. См. `docs/evidence/i-028-egress-diagnostic-20261102T191650Z-90138cda.json`, ADR-071/072. |
| Независимый узел | I-019 bounded provider-failure receipt от 2026-11-01 прошёл A/B egress, recovery, settlement и cleanup по distinct origin ASN. | Не доказывает полную независимость upstream-провайдеров или автоматический failover. B/production в этой работе не трогать. |
| Split tunneling | В I-029 уже есть include/exclude режимы, локальное хранение списка, отказ от пустого include списка и запрет изменения при подключённом VPN. В I-028 физически пройден same-UID included→excluded routing. | Это не проверяет все Android/OEM/work profile/Lockdown комбинации. Для owner test использовать include mode с Chrome в выбранном списке, чтобы не гнать фоновые приложения через туннель. Не добавлять новую реализацию: функция уже есть. |
| Phase 6 dependencies | Повторная проверка 2026-11-03 по текущим lock hashes: Python 76 packages clean, npm — 0 в обоих lockfile, Pub — 0 из 27, Maven — 87 records/45 IDs с severity, 0/49 releaseRuntime matches. Go source hashes и published database index не изменились. Receipt: `docs/evidence/i-030-prebeta-dependency-recheck-20261103.json`. | Полная критическая уязвимость не закрыта: Gradle/Flutter/SDK/NDK и прочий build toolchain, OS/container packages, complete SBOM/licences и независимый review. |
| Phase 6 operations | В `TECHNICAL_SPEC.md` определены начальные SLO-цели. Пустой CSV измерений уже подготовлен. | Реальные load/usage-lag, uptime, rotation drill, memory/battery и traffic cost не измерены. Часть этих пунктов требует разрешённого staging окна, часть — времени или физического телефона; они не превращаются в «только телефонные тесты». |
| Beta delivery/support | Signed `com.detourvpn.client` `0.1.0-beta.1`, private Telegram channel и `@DetourVpnHelper` выбраны; runbooks обновлены. | APK ещё не опубликован владельцем; независимый проход первого тестера по инструкциям не проведён. BitLocker recovery-key file заявлен владельцем как существующий, но доступность/содержимое не проверялись. См. `docs/evidence/i-030-owner-ops-confirmation-20261102.json`. |

Сейчас ADB не показывает подключённого устройства. Задачу backup вручную не
запускать. Никакие quota credits, staging installs, host cleanup, B/production
запросы и публикация APK этим runbook не выполняются.

## I-027: естественный backup и restore

### До 07:00

- Оставить задачу `DetourVpn Beta Backup Daily` включённой; не вызывать
  `Start-ScheduledTask`, `-RunHere` или `-StartScheduledTask`.
- Убедиться, что Windows-компьютер включён, обычная учётная запись владельца
  активна, сеть доступна, а task principal/trigger не изменены.
- Снять read-only baseline task history и списка уже имеющихся архивов. Не
  открывать, не печатать и не копировать `backup.key`.

Команда только читает статус задачи:

```powershell
Get-ScheduledTaskInfo -TaskName 'DetourVpn Beta Backup Daily' |
    Format-List LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns
```

### После 07:00

- Сравнить `LastRunTime`, `LastTaskResult`, `NextRunTime` и число пропусков с
  baseline. Естественный trigger должен иметь время около 07:00, а не быть
  ручным повтором.
- Проверить появление нового зашифрованного архива в
  `%LOCALAPPDATA%\DetourVpn\beta-backup`; записать имя, размер и SHA-256, не
  раскрывая key file.
- Аутентифицировать и восстановить **именно этот архив** штатным I-027 import/
  restore helper в disposable PostgreSQL. Сверить 42-table manifest и ledger;
  затем проверить, что временные database/cluster остановлены и удалены.
- Если расписание не сработало, зафиксировать фактические task result/event и
  сначала разобрать причину. Catch-up проверять отдельно: прошлый тест с
  пропущенным trigger остался `Ready` и не доказал catch-up.

## Owner VPN, split tunneling и подтверждение revoke

### Read-only preflight перед физической сессией

Из корня checkout (PowerShell; скрипт запросит Telegram ID, пароль и TOTP только
в скрытых локальных prompts):

```powershell
python .\scripts\i030_beta_owner_preflight.py
```

Preflight проверяет ровно один авторизованный Pixel 8 Pro, beta.20/versionCode 21
и SHA-256 установленного base APK против самого нового подписанного beta.20
receipt с прежним сертификатом подписи, отсутствие
активного VPN/Always-on/lockdown, серверное состояние `closed`, отсутствие
не закрытых соединений и резервов, а также не менее `1,048,576` доступных bytes.
Сохраняется обезличенный receipt. ID, пароль и TOTP вводятся только в скрытых
локальных prompts; не вставлять их в чат, shell transcript или evidence. Если
любой guard не проходит — Connect не выполнять и quota не менять.

### Минимальный физический прогон

1. При выключенном VPN открыть в beta app «Разделение трафика».
2. Выбрать «Через VPN только выбранные», выбрать только Chrome (`com.android.chrome`)
   и сохранить. Список приложений остаётся на устройстве; не включать остальные
   приложения и не менять настройки при активном туннеле.
3. Подключить beta VPN и дождаться app-confirmed свежего handshake. Не считать
   анимацию или `native up` подтверждением без handshake.
4. В Chrome выполнить один запрос к заранее выбранному фиксированному HTTPS
   egress endpoint и записать только enum результата (`staging_a_egress=true/false`)
   и факт успешного ответа. Не запускать speed test, обновления, видео или фоновые
   загрузки; не сохранять URL history, DNS, внешний IP или payload в receipt.
5. Нажать Disconnect в beta app. Дождаться исчезновения сообщения
   «Ожидается подтверждение удаления peer на сервере» и очистки `revoke_unconfirmed`.
   Серверный DELETE сначала переводит peer в `revoke_pending`; клиент повторяет
   revoke/poll, пока agent не подтвердит close receipt и сервер не вернёт
   `state=closed`. Controller очищает peer только на `closed`; исчезновение
   pending banner после такого ответа — API-level server acknowledgement. Первый
   UI disconnect или `state=revoke_pending` gate не закрывает. Backend-модель:
   `services/api/src/vpn_api/tunnels/window_routes.py`; клиентская обработка:
   `apps/client/lib/window_connection.dart`. Отдельный owner-authenticated GET
   `/api/v1/window-tunnels/{id}` тоже отдаёт state, но его сырой JSON нельзя
   печатать/сохранять: активный ответ может содержать `client_config`.
6. После подтверждения выполнить свежий `VerifyOnly` quota snapshot: `reserved=0`,
   учтённые bytes сходятся с окном/receipt, активного owner peer нет. Node helper с
   legacy Tunnel/PeerCommand counters не подтверждает v3 peer occupancy.
7. Если egress не подтверждён или остаётся `revoke_unconfirmed`, выключить VPN,
   ничего не запускать повторно, снять только sanitized status/quota snapshot и
   сохранить fail receipt. Не начислять quota и не повторять попытку вслепую.

Квота, выданная ранее владельцем, не является разрешением на безлимитные повторы.
Свежий preflight важнее старых балансов. На прошлой попытке фоновой full-tunnel
трафик сделал attribution невозможной; include-only Chrome ограничивает этот риск.

## I-028 / I-029: что повторять на телефоне

I-028 Egress receipt свежий и source hashes совпали; повторять его сейчас не нужно.
ADR-072 убрал широкий AP packet audit только из личного I-028 checkpoint. Общая
спецификация по-прежнему требует реальных DNS/IPv6 leak checks до широкого privacy
claim. Для общего release audit нужен отдельно подготовленный direct positive
control и синхронная контролируемая сеть/capture; отсутствие пакетов на старом
source port или отсутствие IPv6-маршрута не является pass. Linux AP `br-i028` —
допустимая лабораторная точка только после read-only проверки интерфейсов и
прямого IPv6 на телефоне; текущий WSL `eth0` не заменяет AP capture.

I-029 physical acceptance matrix отдельно должна подтвердить на целевом Android:

- сохранение выбора include/exclude после перезапуска app;
- included app использует VPN egress, а excluded app — обычную сеть;
- DNS и IPv6 соответствуют явно выбранной политике, включая Private DNS;
- эффект Android Lockdown для приложений вне списка показан и не замалчивается;
- restart/service failure не восстанавливает просроченный lease и не включает
  туннель без серверной авторизации.

Beta release manifest сейчас opt-out от Always-on; не рекламировать Always-on,
Lockdown kill switch или device-wide leak protection. Физически тестировать
Always-on можно только если владелец решит включить эту функцию в конкретный
release candidate и этот APK будет собран/подписан после этого изменения.

## Phase 6: закрываемая до телефона часть и измерения

### Security, зависимости и supply chain

- На каждом release-candidate hash повторить lockfile scans для Python, npm, Pub,
  Maven и Go; сохранить версии scanner/database и точные lock/source hashes.
  Повторный текущий scan уже записан в
  `docs/evidence/i-030-prebeta-dependency-recheck-20261103.json`.
- Для 87 Maven match records подтвердить точные Gradle configurations, fixed
  versions и отсутствие попадания в release runtime; отдельно закрыть Critical /
  High в debug/test build chain, включая Netty и Guava findings.
- Пересканировать pinned Flutter SDK, Gradle wrapper, AGP/KGP, Android SDK/NDK,
  Go toolchain, OS/container base images и license inventory. SBOM сделать для
  фактического подписываемого APK и серверных images. Текущая машина не имеет
  запущенного Docker daemon и `syft`/`trivy` в PATH, поэтому container SBOM здесь
  не создан.
- Локальный Flutter SDK — `3.47.7`; его included build по-прежнему сообщает
  embedded KGP `2.2.20`. App-level plugin pin `2.4.21` собирается, но это не
  исправляет embedded SDK. Официальный [Flutter SDK archive](https://docs.flutter.dev/install/archive)
  указывает target `3.50` на November 2026; дождаться стабильного upstream релиза,
  проверить changelog и совместимость, затем повторить чистый build/scan. Не
  переводить beta на beta/main SDK без отдельного решения.
- Официальный [Go vulnerability database index](https://vuln.go.dev/index/db.json)
  при проверке оставался `2026-10-27T21:16:18Z`. Повторить `govulncheck` Android arm64/amd64 на новом
  published snapshot или перед окончательным release decision; прошлый scan
  подтверждал ноль reachable symbols для Android target на использованном snapshot.

### Нагрузки, учёт, SLO и стоимость

`TECHNICAL_SPEC.md` задаёт начальные цели, а не публичные гарантии: API availability
99.5% за месяц на ограниченном тесте; API p95 <500 ms без внешних провайдеров;
95% usage records ≤120 секунд при здоровой сети; node failure detection <2 минут
при здоровом мониторинге; RPO ≤24 часа/RTO ≤2 часа в тестовом контуре.

До любого staging load заранее фиксировать build hash, A-only test contour,
исходную загрузку API/DB, concurrency/ramp/duration, точку остановки и cleanup.
Измерять p50/p95/p99 latency, errors, очередь, CPU/RAM/DB pool, usage ingestion lag,
duplicates/drops, reserved/unknown/open bytes и точный settlement. Не выводить
SLO из одной короткой сессии; uptime требует наблюдения во времени. Не подключать B,
production или создавать внешнюю нагрузку без отдельного разрешённого окна.

На Pixel отдельно измерить idle и VPN одинаковой длительности/яркости/радио/сети,
записать app/process memory и battery delta, температуру и build. Не собирать URL,
DNS names или payload. Численная battery цель и бесплатный traffic ceiling в
спецификации не заданы: выбрать их только после baseline и зафиксировать допущения
в ADR. Рассчитывать стоимость на фактический egress price/overhead и измеренный
delivered GiB; не приписывать цену по одному короткому probe.

Пустой CSV: `docs/evidence/i-030-phase6-measurements-template-20261102.csv`.
Оставлять агрегаты и sanitized receipt; не записывать account IDs, tokens, serial,
IP-адреса пользователей, домены, URL или содержимое трафика.

## Одна телефонная сессия: порядок

1. Запустить read-only preflight и проверить свежую quota; при fail не начинать.
2. Настроить include-only Chrome.
3. Выполнить один owner connect/handshake/HTTPS egress/disconnect/revoke/settlement.
4. Подтвердить API `state=closed`, `reserved=0`, отсутствие owner peer и отсутствие
   тестового VPN на телефоне.
5. Если I-029 split settings входят в этот release candidate, проверить сохранение
   настроек после перезапуска приложения. Для общего privacy release-gate отдельным
   окном выполнить синхронный DNS/IPv6 audit на сети с прямым IPv6.
6. Не запускать второй I-028 staging suite без нового основания: текущий receipt
   прошёл и его source hashes совпали.
7. После каждого шага остановиться при неожиданном расходе, ошибке handshake,
   `revoke_unconfirmed`, расхождении ledger или неполном cleanup.

## Текущие локальные проверки 2026-11-03

- `flutter test test/split_apps_test.dart` — 4 passed.
- Полный Flutter test suite — 106 passed.
- `scripts/build_flutter_client.ps1 -WindowTunnels` — Pub lock enforced, format и
  `flutter analyze` passed, 106 tests passed, debug APK, Android unit tests/lint и
  release-mode APK compilation passed. Build — compile evidence; release APK не
  установлен/не опубликован. `apksigner` подтвердил, что этот локальный release
  APK не подписан (не прошла verification из-за отсутствующей signature
  manifest); это не новый подписанный beta candidate.
- API targeted pytest — 32 passed, 43 skipped; DB cases skipped, потому что
  `VPN_TEST_DATABASE_URL` отсутствует; bounded I-028 live test skipped, потому что
  staging run не запрашивался. Ruff по затронутым API window modules — passed.
- `uv export --locked --all-groups` + `pip-audit 2.10.1 -s osv` — 76 locked
  packages, no known vulnerabilities; Pub OSV повторно — 0/27; Maven OSV
  повторно — 87 records/45 IDs с 1 Critical, 18 High, 23 Moderate, 3 Low и 0
  matches среди 49 releaseRuntime coordinates; Go source hashes и DB index
  совпали с текущим предыдущим source-bound scan. Полный summary:
  `docs/evidence/i-030-prebeta-dependency-recheck-20261103.json`.
- Текущий `npm audit --package-lock-only` — 0 vulnerabilities для mini-app и bot.
- Прямой `flutter analyze` из OneDrive path завершился ошибкой протокола
  analysis server (`FormatException` на усечённом JSON); тот же analyzer внутри
  штатного изолированного build pipeline завершился `No issues found`.
- Первый прямой Gradle вызов остановился на stale prepared SDK cache. После
  подготовки новой локальной копии в `.cache/i-011/sdk-go-only-20261103` штатный
  pipeline прошёл. Gradle сообщил предупреждения о скором прекращении поддержки
  текущих Gradle 8.14.3/AGP 8.13.0 в будущей версии Flutter, embedded Kotlin
  2.2.20 и NDK mismatch (app 27.0.12077973, integration_test 28.2.13676358).
  Они требуют toolchain review/обновления на совместимом stable SDK, но текущая
  сборка их не блокирует.
- `adb devices -l` — список пуст. Docker CLI установлен, но daemon не запущен.

## Release stop conditions

- Не объявлять beta ready только по локальным тестам или отдельному physical pass.
- Не выдавать новый quota credit, не запускать backup вручную, не менять task,
  серверные конфигурации, beta/production или provider settings этим планом.
- Не считать `revoke_pending`, legacy node counters, mock UI, hostname DNS failure,
  отсутствие direct IPv6 или старый aggregate capture доказательством закрытия.
- APK distribution — отдельное owner действие после согласования final signed
  artifact/hash, готовности backup/revoke/security/operational gates и review
  runbooks на реальном тестере.
- При неясном target (battery threshold, cost ceiling, Always-on scope или полный
  privacy audit) фиксировать измерения и варианты; не придумывать критерии.

## 2026-11-03 beta.12 follow-up

- Read-only ADB now sees the Pixel 8 Pro with `com.detourvpn.client`
  `0.1.0-beta.12` / version code 13 installed and no active VPN transport.
  The fresh attempt failed at `libbox_service_start`, fixed category
  `awg_device_up`, and `proxyerror`; see
  `../evidence/i-030-beta12-awg-start-diagnostic-20261103.json`.
- Pinned-source review found the AWG bind adapter passed a nil context into the
  direct packet dialer. The minimal context propagation fix passes
  `go test ./transport/awg`, the patched AAR build and Android release Java
  compile. The signed beta.13 build was not completed because owner-local JKS
  password entry is required. No beta.13 APK was installed and no new Connect
  was issued.
- Server-side peer closure remains `not independently verified`; do not issue
  another Connect until a read-only server state check confirms `closed`.
- The natural 2026-11-03 backup trigger was observed but failed with scheduler
  result 1 and produced no new locally authenticated/imported archive. No
  manual retry was performed; the next natural trigger is 2026-11-04 07:00
  Europe/Moscow. See `../evidence/i-027-scheduled-run-20261103.json`.
- The official Go vulnerability database advanced to
  `2026-11-02T22:01:43Z`; both Android source scans were rerun with unchanged
  results (58 records / 47 IDs, no reachable vulnerable symbols). The full
  SBOM/licence/toolchain, operating measurements, DNS/IPv6, physical revoke,
  tester-onboarding and distribution gates remain open.

### 2026-11-03 beta.12 owner follow-up

- Owner reported the app shows `Native Start Failed` / `Native Tunnel Start`
  and no visible `revoke_unconfirmed` banner. This UI observation does not
  independently establish server peer closure.
- Read-only ADB at 22:21 local captured two fresh failures at 22:20:55 and
  22:21:03: `libbox_service_start`, category `awg_device_up`, `proxyerror`;
  no VPN transport was established. This was initially attributed to a
  nil-context source hypothesis, later shown insufficient by beta.13. Do not
  retry Connect until app reconciliation or an owner-authenticated status check
  confirms the previous lease is closed.

### 2026-11-03 beta.13 physical update

- The first signed rebuild was rejected for physical use because it still had
  beta.12 metadata. Android `versionCode`/`versionName` were advanced to 14 /
  `0.1.0-beta.13`, then the normal physical-test build produced a verified
  signed APK (`cc23b8ab…ea946620`, signer unchanged). Build receipt:
  `../evidence/i-030-beta-apk-20261103T192925690Z-7ae1a6db.json`.
- The APK was installed over beta.12 on the Pixel; package query confirms
  beta.13/code 14 and the VPN transport is inactive. Install receipt:
  `../evidence/i-030-beta-apk-install-20261103T193040Z.json`.
- Owner performed two beta.13 connection attempts. Both still failed at
  `libbox_service_start` / `awg_device_up` (`proxyerror`), with no VPN
  transport. The patched native binary is present in the APK, so the
  nil-context fix alone is not the complete cause. Sanitized receipt:
  `../evidence/i-030-beta13-native-start-failure-20261103.json`.
- Beta.14 adds safe allowlisted cause categories for UDP/context errors; one
  bounded diagnostic retest remains pending.

### 2026-11-03 beta.14 diagnostic build

- `:app:testDebugUnitTest` passed: 30 tests, 0 failures; main and test Java
  sources compiled. The physical-test APK is signed and installed as
  `0.1.0-beta.14` / code 15 (`b61b3afa…e312600`); package query and launch
  passed, and no VPN transport was active after installation. See
  `../evidence/i-030-beta-apk-20261103T194244808Z-cdd6f885.json` and
  `../evidence/i-030-beta-apk-install-20261103T194408Z.json`.
- The diagnostic reports only allowlisted cause categories and retains no raw
  exception text. Owner's single physical attempt still failed at
  `libbox_service_start` / `awg_device_up`; no specific allowlisted subcause
  matched and no VPN transport came up. Receipt:
  `../evidence/i-030-beta14-native-start-failure-20261103.json`.

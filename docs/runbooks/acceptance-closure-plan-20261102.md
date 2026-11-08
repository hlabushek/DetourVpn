# План закрытия открытых beta acceptance gates

Дата подготовки: 2026-11-02. Это координационный runbook для I-027, owner VPN/revoke,
DNS/IPv6 privacy evidence и Phase 6 I-030. Он не меняет статусы issues и не
заменяет их acceptance criteria. Рабочее дерево уже содержит другие незакоммиченные
изменения; этот файл подготовлен отдельно.

## Очерёдность

| Очередь | Gate | Что можно подготовить сейчас | Что закрывает gate |
|---|---|---|---|
| 1 | I-030 dependency triage | OSV severity triage выполнен локально по четырём lock-файлам; см. новый receipt | Разобрать build/test tooling advisory и закрыть оставшийся SDK/NDK/native/OS/SBOM scope |
| 2 | I-030 local measurement prep | Использовать измерительные цели из `TECHNICAL_SPEC.md`; внешнюю нагрузку не запускать | Разрешённый staging load/usage-lag measurement; physical Android battery/memory baseline |
| 3 | I-027 natural backup | Не запускать задачу вручную; сохранить автоматический trigger | Успешный естественный запуск 2026-11-03 в 07:00 Europe/Moscow и верификация нового зашифрованного архива |
| 4 | Owner VPN/revoke | Сверить версию APK, ADB и квоту только read-only helper | Контролируемый physical connect/egress/disconnect с серверным подтверждением закрытия и точным ledger settlement |
| 5 | IPv6 для owner I-028 | Принятый ограниченный IPv6-block критерий уже подтверждён ADR-071/072; повтор AP-захвата не нужен | Для личного checkpoint дополнительных IPv6-прогонов нет; широкий audit отдельно остаётся release-вопросом |

Физические прогоны не начинать, пока не завершились backup-наблюдение и локальный
Phase 6 prep, если эти работы доступны раньше. За один визит к телефону подготовить
две отдельные сессии: сначала owner beta app connect/revoke, затем после
подтверждённого закрытия и cleanup — изолированный I-028 Egress suite, если он
нужен для текущего source-bound acceptance. IPv6 AP capture для личного
checkpoint повторять не нужно. DNS/source-port evidence из I-028 относится к
отдельной test UID и не описывает owner beta app. Сохранять отдельные receipts;
общий успешный шаг не делает остальные принятыми.

I-028 Egress runner теперь source-bound готов: 94/94 inputs совпали, Flutter
analyze и 106 unit tests прошли, client/probe instrumentation APK собраны.
Sanitized receipt: `../evidence/i-028-egress-build-20261102.json`. Первичная
сборка выявила устаревшую package-name проверку; её исправили и пересобрали.
Сборка не запускала Stage/Install/Test и не обращалась к beta/staging hosts.
На текущем read-only preflight ADB не показал подключённых устройств. Доступный
WSL Kali видит `tcpdump`, но только `eth0`; этот capture interface сейчас не
готов для прежнего Linux AP. Это не блокирует личный I-028 checkpoint: ADR-072
снимает повторный AP-захват, а bounded IPv6-block evidence уже принято. Когда
появится телефон, всё ещё нужны ADB/status, owner quota snapshot и проверка
server-side cleanup до физической owner-сессии.

## 1. I-027: естественный daily backup

Задача `DetourVpn Beta Backup Daily` уже запускалась вручную 2026-11-02 и
создала проверенный архив. Это не доказывает естественный триггер. До
2026-11-03 07:00 Europe/Moscow:

Read-only snapshot 2026-11-02: задача `Ready`, последний запуск 2026-11-02
15:04:15 с кодом 0 (ручной), следующий запуск 2026-11-03 07:00:00,
`NumberOfMissedRuns=0`. Зафиксировать этот snapshot как baseline; повторить
`Get-ScheduledTaskInfo -TaskName 'DetourVpn Beta Backup Daily'` после триггера.
Команда не запускает задачу.

1. Не выполнять `Start-ScheduledTask`, `-RunHere` или helper с параметром
   `-StartScheduledTask`.
2. Убедиться, что Windows-хост включён, владелец вошёл в свою обычную учётную
   запись, сеть доступна, и задача не отключена. Не менять trigger/principal.
3. Снять read-only исходное состояние `Get-ScheduledTaskInfo` и локальный список
   уже имеющихся архивов с их временем, размером и SHA-256. Не читать и не
   выводить backup key.
4. После планового времени проверить `LastRunTime`, `LastTaskResult` и новый
   зашифрованный файл в `%LOCALAPPDATA%\DetourVpn\beta-backup`. Совпадение
   времени, нового файла и результата задачи должно отличать естественный запуск
   от ручного.
5. Проверить импорт/аутентификацию архива штатным локальным helper. Для полного
   restore evidence использовать отдельную disposable PostgreSQL процедуру из
   существующего I-027 runbook; сверить manifest и ledger, затем подтвердить
   удаление временной БД/кластера.
6. Если естественный запуск не состоялся, сохранить failure facts. Не запускать
   задачу вручную до фиксации результата; `StartWhenAvailable`/catch-up проверить
   отдельным bounded экспериментом и не считать доказанным заранее.

Команды задачи и backup-only архитектура задокументированы в
[`beta-environment.md`](beta-environment.md#ежедневный-backup-i-027). Последний
restore receipt: `../evidence/i-027-restore-drill-20261102.json`; он относится к
ручному запуску и не закрывает natural-trigger gate.

## 2. Owner VPN и серверное подтверждение отзыва

Подготовка до подключения:

1. Запустить единый безопасный preflight из PowerShell 7:
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\preflight_i030_owner_window.ps1 -VerifyQuota`.
   Он проверяет ровно одно разрешённое ADB-устройство, Pixel 8 Pro, beta package,
   version `0.1.0-beta.1`/code 2 и SHA-256 base APK из подписанного receipt,
   а также требует выключенный VPN. Серийный номер и сырые ADB dumpsys не выводятся
   и не сохраняются. Затем `-VerifyQuota` запускает только read-only helper ниже.
   Если версия/hash не совпадает, устройство неоднозначно, другой VPN активен или
   квота ниже 1 MiB — остановиться; не начинать подключение.
2. `-VerifyQuota` в preflight запускает read-only quota helper. Он локально
   запрашивает owner ID, пароль и TOTP, читает quota и завершает admin session;
   секреты вводить только в локальные secure prompts. Вызов без `-VerifyOnly`
   запрещён: обычный режим может начислить quota. Минимум клиента — 1 MiB
   (`1,048,576` bytes). Snapshot 2026-11-02 показал 1,040,137 bytes,
   дефицит 8,439 bytes. После явного разрешения владельца отдельный строгий
   snapshot-bound helper начислил 256 KiB: итог доступно 1,302,281 bytes.
   Последующая попытка израсходовала 1,047,601 bytes и завершилась с нулевым
   резервом; ping был отправлен уже после отключения VPN и в результат не
   засчитывается. По явному разрешению владельца helper затем начислил 64 MiB;
   итог доступно 67,363,544 bytes, резерв 0. Не повторять начисление без новой
   необходимости и свежего snapshot. Receipts:
   `../evidence/i-030-owner-window-credit-20261102T203715Z.json` и
   `../evidence/i-030-owner-window-credit-20261102T204537Z.json`. Перед новым
   прогоном повторить VerifyOnly и ADB preflight. Последний snapshot после
   неудачного прогона показывал 55,433,255 bytes available; это не текущий баланс,
   свежий VerifyOnly обязателен.
3. При выключенном VPN включить split mode «Через VPN только выбранные», оставить
   один тестовый app (Chrome), остальные apps закрыть. Сначала открыть фиксированный
   HTTPS oracle в Incognito с VPN off и убедиться, что он отвечает. Записать
   текущие split настройки для восстановления после прогона; фактический IP не
   сохранять.
4. Во время одного короткого окна подключить VPN, дождаться подтверждённого
   handshake. Пока VPN включён, выполнить
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\preflight_i030_owner_window.ps1 -ConnectivityOnly`;
   ожидать `active_vpn_transport=true`; при `false` сразу отключиться и остановить
   прогон. Затем открыть новый Incognito tab и запросить `https://api.ipify.org`
   один раз. Сравнить ответ с известным staging A egress `45.10.166.106` и direct
   baseline; записать только `matches_staging`, `different_from_direct_baseline`
   или `failed`. Не запускать `adb shell ping`: он не является HTTPS egress
   acceptance.

Во время прогона связать в одном временном окне: свежий handshake, ограниченную
HTTPS контрольную передачу, нажатие Disconnect, итоговый диагностический код и
состояние server-pending banner. После disconnect проверить отдельно:

- Android native VPN transport выключен;
- баннер «Ожидается подтверждение удаления peer на сервере» исчез после ответа
  v3 API `state=closed`; отсутствие баннера — серверное подтверждение отзыва,
  когда до Disconnect handshake был подтверждён;
- ledger сходится: granted = used + available + reserved по правилам ledger,
  `reserved_bytes=0`, неизвестных/open receipts нет;
- обычная сеть восстановлена, прежние split-настройки возвращены. Этот owner-прогон
  не устанавливает временный grant, staging или probe APK.

После Disconnect запустить:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\preflight_i030_owner_window.ps1 -ConnectivityOnly
```

Ожидать `active_vpn_transport=false`. Затем запустить основной preflight с
`-VerifyQuota` для итоговой APK/VPN/ledger-сверки. Если вывод до/во время/после
не совпадает с ожидаемым, остановить прогон и записать факт; не переподключать VPN.

Если pending-banner не исчезает, сохранить revoke gate открытым. Не выводить успех
из одного только локального отключения телефона. Оставить VPN выключенным, дождаться
автоматического polling/истечения bounded lease и повторить только read-only
проверку; не создавать новое соединение поверх pending revoke.

`& '.\scripts\check_beta_node_status.ps1' -RunHere` — безопасный read-only
status snapshot, но его legacy `Tunnel`/`PeerCommand` counters не показывают v3
window peer occupancy и не могут подтвердить revoke. Для v3 учитывать только
сценарный receipt с server close/settlement evidence; при отсутствии такого
поля результат не принимать.

Последняя физическая попытка 2026-11-02: владелец сообщил о подтверждённом
handshake; ADB увидел активный VPN agent во время двух IPv4 probe, но ответов не
было. После disconnect ADB увидел ноль VPN agents; ledger учёл 11,930,289 bytes,
reserved 0. Observer первоначально использовал слишком строгий matcher и не
распознавал mixed transport list; тайминг и источник полного объёма трафика не
подтверждены. Профиль маршрутизирует IPv4 default route, поэтому фоновые
приложения могли участвовать. Владелец сообщил, что после disconnect ошибок не
было; код клиента ставит `revoke_unconfirmed`, если DELETE не вернул `closed`, но
ответ API отдельно не захвачен. Не повторять connect/probe до разбора node egress
и подготовки короткого окна с ограниченной фоновой активностью. Receipt:
`../evidence/i-030-owner-window-egress-failed-20261102T204947Z.json`.
Последующий node status сообщил `active`, protocol 3 и `last_seen_present=true`.
Это подтверждает heartbeat control plane, но его legacy-счётчики не показывают
v3 occupancy или data-plane egress. Не использовать `adb shell ping` как критерий:
ICMP может быть запрещён, а shell UID не является выбранным приложением. Предыдущие
11,930,289 списанных bytes нельзя приписать двум ping; при полном IPv4 route
возможен фоновый расход. Не требовать глобальное `occupied=0`: второй слот может
быть занят другой сессией. Для итогового receipt использовать форму
`docs/evidence/i-030-owner-window-retest-template.json`; фактический IP, секреты,
DNS-имена, URL историю и payload не сохранять. I-028 Egress APK/runner — отдельный
test UID и сценарий, он не заменяет owner evidence.

Источники: `docs/evidence/i-030-owner-window-revoke-followup-20261102.json`,
`docs/evidence/i-030-owner-window-retry-20261102.json` и текущая реализация
disconnect в `apps/client/lib/window_connection.dart`.

## 3. IPv6 scope: личный checkpoint уже принят

ADR-071/072 задают личный I-028 scope как IPv4-only VPN с блокировкой IPv6 у
включённого приложения. На 2026-10-31 он прошёл ограниченный физический probe:
без VPN тот же фиксированный source port открыл IPv6 literal; под проверенным
VPN тот же сокет попытался подключиться и получил `io`, при этом staging IPv4 и
fixed UDP DNS работали. Синхронный 90-секундный AP capture увидел VPN outer
trigger и ноль пакетов source port `42429`. В capture также было четыре
агрегатных IPv6-пакета без UID/app attribution — они не отменяют fixed-socket
результат и не доказывают device-wide leak.

Раньше это проверяли через Linux laptop с USB Wi-Fi AP и bridge `br-i028`,
захватывая агрегатные счётчики без PCAP. Windows Mobile Hotspot тогда не дал
телефону global IPv6/default route и не был пригоден как direct IPv6 baseline.
Текущий WSL `eth0` не является тем AP capture point. Однако повторять настройку
Linux AP или делать новые IPv6-физические прогоны для личного checkpoint не
нужно: это прямо снято ADR-072. Полный DNS/IPv6 audit остаётся отдельным будущим
product/release вопросом; текущие evidence не дают общего leak-free заявления.

## 4. I-030 Phase 6: работа до появления физического устройства

### 4.1 Уязвимости и зависимости

Базовый receipt `docs/evidence/i-030-dependency-audit-20261102.json` фиксирует
zero findings для locked Python runtime, двух npm lockfiles, 27 hosted Pub
packages и 49 beta `releaseRuntimeClasspath` координат. Новый
`docs/evidence/i-030-maven-advisory-triage-20261102.json` уточнил исходные 87
Maven match records: 45 уникальных ID (1 CRITICAL, 18 HIGH, 23 MODERATE, 3 LOW),
0 совпадений в beta release runtime. Все critical/high матчи — в Gradle
debug/test/internal Android test-platform configurations; один HIGH Guava advisory
затрагивает beta app только в `debugRuntimeClasspath` и `debugUnitTestRuntimeClasspath`.
Это не release-runtime finding, но build/test toolchain всё ещё требует triage.

Порядок продолжения:

1. Сверить SHA-256 lockfiles с receipt; если файлы изменились, считать прежние
   нулевые результаты устаревшими и пересканировать.
2. Для 87 advisory records раскрыть OSV IDs, affected/fixed ranges и severity из
   advisory source. Дедуплицировать по advisory ID + coordinate, поскольку число
   записей не равно числу уникальных CVE. Отдельно отметить реально разрешённые
   конфигурации и test/build-only зависимости. Не пропускать advisory только
   потому, что версия вне `releaseRuntimeClasspath`.
3. Установить, какие advisory затрагивают production/release build и какие
   требуют обновления/mitigation. Перед изменением lockfiles проверить обратную
   совместимость, upstream maintenance, license и supply-chain последствия;
   не делать массовое обновление только ради уменьшения счётчика.
4. Расширить inventory на Gradle wrapper/plugins, Android SDK/NDK, native Go и
   vendored dependencies, OS/container base images; сгенерировать полный SBOM и
   проверить license inventory. Зафиксировать отдельно пропуски сканеров.
5. Выпустить новый sanitized receipt с инструментами/версиями, hashes, scopes,
   timestamp, advisory IDs/status, false-positive rationale и подтверждением,
   что secret/token values не попали в вывод. После исправлений пересканировать
   точные итоговые artifacts.

Go supplemental triage в
`docs/evidence/i-030-go-advisory-triage-20261102.json` содержит 54 OSV match
records, включая 7 CRITICAL и 2 HIGH для `golang.org/x/crypto v0.42.0`.
Дополнительный Android-target source symbol scan выполнен для `android/arm64` и
`android/amd64` на Go 1.25.14/NDK r26b: обе команды `govulncheck v1.7.0` завершились
с кодом 0; в каждом потоке 58 finding messages / 47 уникальных ID: 57
module-level, одна package-level и ноль symbol-level. Package-level запись
`GO-2026-6604` относится к Windows-only `os.Root` junction operations и не
применяется к Android target. Target import graph обеих ABI использует crypto
примитивы, но не `golang.org/x/crypto/ssh`. Это сужает применимость SSH-related
module matches для этих исходников; не считать это runtime-proof или общим
закрытием критического gate. Официальный `index/db.json.gz`, проверенный
2026-11-02, сам сообщает `modified=2026-10-27T21:16:18Z`; обе target scans
использовали этот текущий опубликованный snapshot. Проверить дату снова перед
следующим release decision. Результат и hashes:
`docs/evidence/i-030-go-android-target-scan-20261102.json`. Не обновлять x/crypto
без проверки совместимости WireGuard/AmneziaWG и нового target scan.

Дополнительный OSV query обнаружил GHSA-r937-wjx7-w2jp / CVE-2026-53914 в
`org.jetbrains.kotlin:kotlin-gradle-plugin:2.2.20`, объявленном в
`apps/client/android/settings.gradle.kts` и не входившем в предыдущий lockfile
query. App-level plugin обновлён до 2.4.21; Gradle 8.14.3 и AGP 8.13.0 входят
в опубликованную Kotlin compatibility matrix, штатные Flutter/Android checks
прошли. Но pinned Flutter 3.47.7 SDK использует KGP 2.2.20 внутри
`packages/flutter_tools/gradle/build.gradle.kts`. SDK локально не патчился,
поскольку это нарушило бы pinned revision/archive hash; toolchain gate остаётся
открытым до проверенного upstream stable Flutter update. Receipt:
`docs/evidence/i-030-kotlin-build-plugin-triage-20261102.json`.

### 4.2 Load, accounting lag, memory/battery, SLO и стоимость

Начальные MVP targets уже заданы в `TECHNICAL_SPEC.md` §11: API availability
99.5% в месяц на ограниченном тесте; API p95 <500 мс без внешних provider calls;
95% usage records не старше 120 секунд при здоровой сети; node failure detection
<2 минут при исправном мониторинге; RPO ≤24 часа/RTO ≤2 часа в test contour.
Это цели, не гарантии. Не объявлять pass по одному baseline. Точные SLO API
availability требуют периода наблюдения; physical throughput, reconnect, battery
и memory требуют реального Android. Cost/free-quota ceiling и численная battery
цель в §11 не заданы; их нельзя додумывать — зафиксировать допущения/решение в ADR
после получения measurement baseline. Подготовить профиль и собирать агрегаты:

- фиксированные build/source hashes, staging node и DB size/config; нагрузка
  ступенями с заранее указанными concurrent sessions, длительностью и ramp;
- API p50/p95/p99 latency, request/error rate, queue depth, CPU/RAM/DB pool;
- accounting lag от приёма usage до durable ledger visibility (p50/p95/max),
  пропущенные/дублированные события, overrun/unknown/open counters; конечный
  ledger settlement должен быть точным, а не статистическим;
- Android экран/радио/яркость/температура/сеть/build фиксируются до и после
  одинакового окна idle и одинакового окна туннельной передачи; измерять delta
  battery и app/process memory. Не собирать личную историю/содержимое трафика;
- cost per delivered GiB и ежемесячная стоимость при явно записанных объёме,
  egress price, включённом трафике и допущениях; сверить расчёт с фактическим
  staging usage/provider bill, не экстраполировать один короткий прогон как SLO.

До физического теста подготовить local load profile, план очистки сущностей,
синхронизированные часы и CSV/dashboard с агрегатами. Пустая схема измерений
подготовлена в
[`i-030-phase6-measurements-template-20261102.csv`](../evidence/i-030-phase6-measurements-template-20261102.csv);
она не содержит строк с пользовательскими данными. Concurrent sessions, ramp,
duration, cost assumptions и численные pass thresholds заполнить только после
выбора разрешённого staging contour и measurement baseline из §11. Не выдавать
тестовую quota.
Внешний нагрузочный прогон проводить только в отдельно разрешённое staging-окно;
не нагружать beta/production host и не создавать дополнительные расходы без
отдельного разрешения. После измерения сопоставить результаты с §11, определить
free-traffic ceiling и записать node rotation drill outcome до объявления Phase 6
принятой.

Go Android-target static reachability дополнила локальную подготовку для двух
ABI; текущий опубликованный Go DB snapshot на дату проверки изменён 2026-10-27.
Перед следующим release decision перепроверить индекс и повторить scan, если он
обновится. Owner VPN/revoke физический тест сейчас остановлен: подключённый Pixel
8 Pro и совпадение signed APK hash подтверждены, но read-only snapshot показывает
1,040,137 доступных байт при минимуме окна 1,048,576 байт. Runbook запрещает
выдавать тестовую quota; продолжать можно после естественного пополнения
allowance. Receipt: `docs/evidence/i-030-owner-preflight-20261102T200453Z.json`.

## Stop conditions и сохранение evidence

- Никаких секретов, ssh passwords, VPN keys, имён/URL/DNS, payload, адресов или
  полных идентификаторов пользователя в чате, receipts и логи.
- Не давать quota credit для теста; не покупать/создавать инфраструктуру и не
  менять beta host или provider settings в рамках этого плана.
- Не запускать физический тест без единственного ожидаемого ADB device,
  разрешённого окна, достаточной квоты, согласованного APK/source hash и
  независимого bounded cleanup.
- Сохранять каждый pass и fail receipt как новую запись. Перед новым запуском
  проверить факты cleanup по телефону, API/node, timer, grants, namespace,
  forwarding и тестовым APK; не полагаться на вывод одной команды `Finish`.
- При несовпадении текущих receipts и read-only состояния сначала зафиксировать
  новый sanitized snapshot. Не переписывать историю и не выводить общий beta-ready
  статус из отдельных component passes.

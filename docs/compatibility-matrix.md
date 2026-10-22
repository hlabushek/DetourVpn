# I-002: матрица совместимости и план сборки

Дата: 2026-10-18. `SOURCE` — подтверждено pinned исходниками; `BUILD` — проверено
реальной сборкой; `DEVICE` — физическим устройством/сетью. В I-002 есть только SOURCE.
`NOT RUN` не является pass. Все revisions/hashes в [receipt](evidence/i-002-sources.json).

## Выбранная пара и reference

| Поле | AWG source-кандидат | WireGuard reference | Evidence |
|---|---|---|---|
| Android repository commit | ff15093bf3856fea0947923474870b86efb682f0 | e7b3a3c118836e112620b1302a8ba1873ad4daac | SOURCE |
| Upstream source versionName | 3.1.4 | 1.0.20260315 | SOURCE; не AAR provenance |
| Embedded Go module | github.com/amnezia-vpn/amneziawg-go/v3 v3.1.20260814 | golang.zx2c4.com/wireguard v0.0.0-20250521234502-f333402bd9cb | SOURCE go.mod |
| Core commit для client/server spike | 1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1 | f333402bd9cbe0f3eeb02507bd14e23d7d639280 | SOURCE proxy Origin |
| minSdk / compileSdk | 24 / 36 | 24 / 36 | SOURCE settings/tunnel Gradle |
| targetSdk нашего приложения | Не выбран, зависит от будущего Flutter/native app | Не выбран | Не путать compileSdk с targetSdk |
| Java target | 17 | 17 | SOURCE |
| NDK | 26.1.10909125 | Здесь не выбран/не подтверждён | SOURCE AWG settings |
| AGP / Gradle | 8.13.0 / 8.13 | Reference toolchain не принимался как наш baseline | SOURCE AWG catalog/wrapper |
| Go requirement / Makefile bootstrap | 1.25.0 / 1.24.2, discrepancy | 1.23.1 / 1.24.3 | SOURCE go.mod/Makefile; BUILD NOT RUN |
| Android protocol/config | AWG 3.1 поля присутствуют в serializer и Go UAPI | WireGuard base config, без AWG parameters | SOURCE, round-trip NOT RUN |
| ABIs planned | arm64-v8a primary, armeabi-v7a optional, x86_64 emulator | Те же тестовые targets после выбора build | BUILD NOT RUN; packaged list неизвестен |
| Native binary inventory / 16 KB alignment | В CMake есть alignment flags; binary не проверен | Не проверен | BUILD NOT RUN |
| Server binary / Linux version / arch | План Linux amd64 userspace, конкретный стенд не выбран | Reference по необходимости | BUILD/DEVICE NOT RUN |

## Сетевые и эксплуатационные проверки I-011

Каждый результат должен содержать revision APK/server binary + hash, Android API,
ABI, устройство, оператор/тип сети, дату, ожидаемый/фактический исход. Не сохранять
private keys, реальные токены, историю DNS/URL или tunnel payload в отчётах.

| Сценарий | Что доказывает | AWG | Reference |
|---|---|---|---|
| Физическое подключение / egress IPv4 | Новый egress IP через выбранный узел | NOT RUN | NOT RUN |
| IPv6 available / unavailable | Правильный маршрут или явный безопасный запрет, без обхода туннеля | NOT RUN | NOT RUN |
| DNS / отказ DNS / dual-stack | Резолвер и маршрут проверены, утечки отсутствуют в тесте | NOT RUN | NOT RUN |
| Wi-Fi → LTE → Wi-Fi | Reconnect и понятный статус без зависания | NOT RUN | NOT RUN |
| Suspend/resume, reboot, foreground rules | Lifecycle и network_changed | NOT RUN | NOT RUN |
| Permission denied/revoked, второй VPN | Нет ложного connected; корректная ошибка | NOT RUN | NOT RUN |
| Device/peer revoke | Реальный узел прекращает доступ, не только UI | NOT RUN | NOT RUN |
| Counter restart/reordered samples | Новый epoch, нет двойного списания | NOT RUN | NOT RUN |
| API outage / node outage / quota expiry | Работает утверждённая bounded lease policy | NOT RUN; ADR-004/008 prerequisites | NOT RUN |
| Logcat/backup/package inspection | Нет secret dumps, проверен фактический native inventory | NOT RUN | NOT RUN |
| Battery / sustained throughput / 16 KB page device | Измеренная устойчивость, не UI-анимация | NOT RUN | NOT RUN |
| Strict allowlist / недоступный endpoint | Честная ошибка unavailable; обещаний универсального обхода нет | NOT RUN | NOT RUN |

## План воспроизводимой сборки: не выполнялся

Этот план не запускает upstream Gradle/Makefile автоматически. Build scripts сами
являются исполняемым сторонним кодом и сначала требуют review.

1. На отдельном локальном Linux build workspace checkout exact SDK commit,
   проверить gitlinks из receipt. Не использовать default branch/плавающие tags.
   Записать ОС, JDK 17 vendor/patch/checksum, Android SDK terms и версии tooling.
2. Проверить и закрепить Gradle 8.13 distribution checksum. Создать reviewed patch:
   устранить Go 1.24.2 vs module 1.25.0, выбрать поддерживаемую Go patch version,
   проверить применимость goruntime patch. Явный GOTOOLCHAIN=local только после
   согласования bootstrap; не допускать скрытого auto-download другой версии.
3. Если выбран Go-only путь, изменить build targets/Java root backend/package
   composition и подтвердить отсутствие исключаемых executables в AAR. Patch
   хранить отдельно с hash и объяснением; не менять crypto/transport алгоритм.
4. Получить Gradle dependency report + lock/verification metadata; проверить
   Maven artefact hashes/POM/NOTICE. Получить `go mod verify`, package/module
   inventory для фактических Android build tags. Никакого `go get ...@latest`.
5. После review выполнить на build host `./gradlew --no-daemon :tunnel:testReleaseUnitTest :tunnel:assembleRelease`.
   Это команда будущего spike, существование/успех задачи ещё не подтверждены.
   Записать stdout/stderr без secrets, toolchain, AAR SHA-256 и содержимое.
6. Проверить все ожидаемые ABIs и ELF alignment, release logging, licenses/SBOM,
   повторить clean build с теми же inputs и сравнить output. Если hashes различны,
   описать причину; не объявлять bit-for-bit reproducibility заранее.
7. Собрать server userspace binary из того же core commit в изолированной среде,
   без запуска tunnel/открытия портов. Проверить provenance и version/capabilities.
   Тестовый VPS и реальные подключения — только отдельные I-010/I-011.

Повторная проверка локального Windows окружения для I-011 (2026-10-18): Java в
PATH — 8, Android Studio JBR — 21.0.5; установлены Android platform 36 и
build-tools 36.0.0. Доступен NDK **28.2.13676358**, тогда как pinned Android
source требует **26.1.10909125**. Go/Flutter/Gradle в PATH не найдены; Android
device по `adb devices -l` не подключён. SDK licence files уже присутствовали,
но новые лицензии за владельца не принимались и недостающие инструменты не
устанавливались. Это inventory, а не успешная сборка. BUILD и DEVICE остаются
NOT RUN; подробности в [I-011 verification](verification-i-011.md).

## Блокирующие действия

| ID | До чего блокирует | Действие / ответственный |
|---|---|---|
| B1 | Native build acceptance | Устранить bootstrap discrepancy и подтвердить runtime patch; developer build spike |
| B2 | SDK/release acceptance | Утвердить упаковку, выполнить per-file/Maven/Go inventory и obligations; developer + владелец |
| B3 | Supply-chain/build reproducibility | Wrapper/dependency verification, pinned toolchain и повторный build; developer |
| B4 | Auth technical contract | Вход кодом из бота уже выбран владельцем; проверить pairing, trusted Updates, сессии/recovery и retention до I-005 |
| B5 | VPN/quotas readiness | I-010/011 + ADR-004/008 и физические evidence; developer на разрешённом стенде |
| B6 | Security acceptance кандидата | Triage OSV findings 6 modules, build-specific govulncheck и исправленная pinned пара; developer до native acceptance |

I-002 research verdict: **GO для дальнейшего анализа и независимого backend foundation;
NO-GO для утверждения/распространения непроверенного native tunnel bundle**.

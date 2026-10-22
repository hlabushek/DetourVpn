# I-015 — результаты и граница приёмки

Дата: 2026-10-21. **I-015 принята и закрыта: автоматические и физические
gates пройдены.** I-016 не начат.

## Финальная физическая приёмка

Владелец выполнил `.\scripts\run_i015_android.ps1` в обычном Windows PowerShell
из репозитория с подключённым Pixel 8 Pro. Вывод: **1 passed, 418 deselected,
42.31s**, disposable PostgreSQL остановлен, кластер удалён; установка обычного
APK — Success. Результат независимо сверен агентом с сохранённым receipt,
а не приписан запуску в ограниченной сессии.

`docs/evidence/i-015-local-android.json` подтверждает passed=true и:

- физический Android, настоящий API и PostgreSQL;
- успешный вход и reward_history_and_replay: history показывает награду,
  повтор тем же ключом и refresh не создают двойного кредита;
- reward_database_consistent: claim/ledger/баланс и reconciliation согласованы;
- admission-closed regression без ложного connected, revoke/settlement;
- остановку приложения, удаление ADB reverse и ordinary_apk_restored=true.

Синтетическая daily rule на 17 байт была только в disposable test DB; доставка
Telegram — TEST FAKE ONLY. Production rewards по-прежнему выключены; новый
VPN egress, Mini App, платежи и production rollout не заявляются.

Итог: PostgreSQL suite **417 passed, 2 skipped**, Flutter **38 passed**, Android
unit **10 passed**, lint clean, debug/release builds и физический тест — passed.
Пропуски полного backend suite относятся к отдельно запускаемым I-012 staging
и I-013 physical; I-015 physical теперь подтверждён отдельным успешным прогоном.
Сводный результат с SHA256 исходных receipts: `docs/evidence/i-015-acceptance.json`.
Build receipt отражает состояние до physical run; его поле
physical_rewards_acceptance=false — исторический снимок, не итоговый статус.

## Актуальный результат после освобождения места

Владелец освободил место; свободное место повторно не проверялось по его просьбе.
Полный `scripts/build_flutter_client.ps1` повторён на актуальном коде, включая
pinning integration_test dependencies к версиям из existing app lockfile.

- Locked pub get: passed; Dart format: **15 files, 0 changes**.
- Flutter analyze: **No issues found (10.0s)**; **38 Flutter tests passed**.
- Обычный `lib/main.dart`: **debug и release APK собраны**. Release build: 57.3s.
- `:app:testDebugUnitTest`: **10 passed, 0 failures/errors/skips**, подтверждено
  экспортированными XML (3 KeyConfirmation + 4 Profile + 3 TunnelStatus).
- `:app:lintDebug`: **No issues found**; Gradle unit/lint run: **BUILD SUCCESSFUL,
  45s**, 121 tasks. Tooling warnings о Gradle/AGP/Kotlin/NDK остаются отдельно.
- Exported artifacts: `apps/client/build/app/outputs/flutter-apk/`;
  текущий обычный debug APK установлен на Pixel, приложение остановлено.
- SHA256 debug: `282d11df8263630c7db8a23b293aed0e22afb60b9b727f8937cad89de0f1ffca`.
- SHA256 release: `f8644d15aec0f0937a997a439ac5fe3180d177a889e545c1076ce1986f16efe7`
  (совпадает с ранее успешно собранным release). Это не public/signed production release.
- Все 15 Dart source/test файлов, Android root Gradle config и pubspec.lock
  совпали с проверенной source copy. Release native registrant не содержит
  IntegrationTestPlugin. Машиночитаемый результат: `docs/evidence/i-015-build.json`.
- Backend полный PostgreSQL прогон владельца **417 passed / 2 skipped** ниже
  сохраняет силу; код продукта после него не менялся, менялся test harness/build.

История ограничения execution session: HTTP probe через ADB reverse превышал
таймаут; Windows listener не принимал соединение от телефона. Diagnostic reverse
был удалён. Запуск в обычном PowerShell впоследствии прошёл; текущий physical
receipt содержит успешный результат и заменяет прежнюю неуспешную попытку.

Воспроизведение physical-проверки **в обычном PowerShell**, из репозитория,
с подключённым и разблокированным Pixel (повторять для закрытия уже не нужно):

```powershell
.\scripts\run_i015_android.ps1
```

Runner заново выполняет offline enforced-lockfile pub get перед debug test
(для правильных путей packages и native plugin metadata после release),
компилирует test entrypoint и запускает настоящий API/disposable PostgreSQL.
В finally возвращает обычный APK. Успешный выход, receipt passed=true,
ordinary_apk_restored=true и cleanup flags подтверждены финальным прогоном.

Запрошенное владельцем удаление двух временных каталогов повторно отклонено
автоматической политикой среды даже после явного разрешения: требуемый
sandbox approval недоступен. Каталоги не удалены. Разрешение владельца
сохраняется; повторно его запрашивать не требуется.

## Предыдущие попытки (история)

- Pixel 8 Pro доступен через ADB; Android emulator flag не установлен.
- Добавлен отдельный local-only I-015 режим существующего physical harness:
  настоящий rewards API, replay, UI history/refresh и сверка с PostgreSQL.
  Test rules синтетические (17 байт); production rules не включались.
- В тестовом integration_test Gradle-проекте зафиксированы уже существующие
  версии из app/gradle.lockfile: runner 1.3.0 и rules 1.2.0. Это устранило
  зависимость offline-сборки от отсутствующего списка версий 1.2+; lockfiles
  и release-зависимости не менялись. Изменён apps/client/android/build.gradle.kts.
- Тестовый APK успешно собран с закреплённым Flutter 3.47.7. Для сборки использованы
  ASCII-копии SDK/source, скопированный Gradle cache в offline-режиме и временная
  копия прежнего debug signing key вне репозитория (с той же подписью).
- Physical pytest: **1 failed, 418 deselected, 82.13s**, стадия login_challenge.
  Награда на телефоне не проверена. Санитизированный результат:
  docs/evidence/i-015-local-android.json. Сервер и disposable PostgreSQL остановлены,
  кластер удалён; обычный main.dart APK установлен обратно и остановлен.
- Дополнительный минимальный HTTP/TCP probe через ADB reverse также превысил
  таймаут; Windows listener не принял соединения от телефона. Это согласуется
  с ограничением доступа к listener текущей execution session; точный механизм
  ограничения не установлен. Нельзя выдавать эту диагностику за проверку rewards.
  Последующее удаление diagnostic reverse подтверждено. Результат:
  docs/evidence/i-015-loopback-diagnostic.json.
- Последний запуск scripts/build_flutter_client.ps1 на актуальном исходном коде:
  locked pub get, Dart format (15 files, 0 changes), analyze (no issues, 8.0s),
  **38 Flutter tests passed**. Android debug сборка остановилась с ошибкой
  «Недостаточно места на диске»; новые unit/lint/release gates не выполнены.
  C: reported Free=0. Предыдущие 10 unit tests/lint/release ниже относятся
  к успешному прогону до последней правки test dependency pinning.
- Ruff check/format, mypy изменённого Python-файла и PowerShell syntax прошли.
- Автоматическая проверка отклонила удаление созданных временных копий, в том
  числе попытку удаления одного избыточного APK после проверки совпадения SHA256:
  действие потребовало sandbox approval, недоступный в этой среде. Ничего
  из запрошенных к удалению файлов таким образом не удалено.

Для продолжения освободить место. Созданные этой работой воспроизводимые копии:
C:\Users\JetGame\AppData\Local\Temp\DetourVpn-i015 (SDK/cache и копия debug key),
.cache/i015-gradle/caches/modules-2 (копия dependency cache),
Temp/ProjectVpn/i013-builds/d0ccca5d7194424fa7a84f232e7d7c8e и
Temp/ProjectVpn/i013-builds/e8ed6d7dbd084e2a9b2c797ab004542d (source/build copies).
Не удалять оригинальные SDK, .android/debug.keystore или исходники.

Затем в обычном PowerShell из репозитория, с разблокированным Pixel:

```powershell
.\scripts\build_flutter_client.ps1
# Только после успешного завершения build runner:
.\scripts\run_i015_android.ps1
```

Второй runner использует existing .venv, либо uv --frozen при её отсутствии,
восстанавливает обычный APK в finally и записывает ordinary_apk_restored.
Закрывать issue только при успешных gates и passed=true/cleanup flags в receipt.

## Предыдущие результаты (история; новые ограничения описаны выше)

Проверено фактическое состояние: I-014 присутствует в коде и имеет verification /
evidence. До этой работы rewards-модуля, таблиц и endpoints не было. Поэтому
I-015 действительно следующий issue. Требования и предположения — ADR-027.

## Успешная локальная проверка 2026-10-21

Владелец выполнил обе команды в обычном PowerShell; полный вывод предоставлен
в чате. Результаты PostgreSQL/Flutter ниже взяты из этого вывода, а не объявлены
повторным запуском агентом. Агент дополнительно проверил локальные Android XML,
lint-отчёт, APK и исходники сохранённой сборки.

- `.\.venv\Scripts\python.exe scripts/test_with_postgres.py --postgres-bin
  .cache/i-004/portable/pgsql/bin`: **417 passed, 2 skipped, 198.10s** из 419.
  Реальный изолированный PostgreSQL остановлен, кластер удалён. Все 13 rewards
  tests и 14 rewards contracts tests прошли, включая повторы/гонки, смену UTC
  суток, cooldown/caps, rollback/overflow, auth/revocation и пустые правила.
  Миграционные и quota regression tests также прошли.
- Пропущены только явно требующие отдельного запуска I-012 staging и I-013
  physical tests; это не пропуски rewards tests и не новое подтверждение VPN.
- `.\scripts\build_flutter_client.ps1`: enforced lockfile, **15 Dart files,
  0 formatting changes**, analyze **No issues found**, **38 Flutter tests passed**.
- Android debug и release APK успешно собраны. Script явно выполняет
  `:app:testDebugUnitTest` и `:app:lintDebug` между сборками; при ошибке любого
  шага прекращает работу. Gradle: **BUILD SUCCESSFUL**.
- Сохранённые XML в `apps/client/build/app/test-results/testDebugUnitTest/`:
  KeyConfirmationTest **3**, ProfileTest **4**, TunnelStatusTest **3**;
  всего **10 passed, 0 failures/errors/skips**, timestamps 18:28:47 UTC.
- `apps/client/build/app/reports/lint-results-debug.txt`: **0 errors, 1 warning**
  (AndroidGradlePluginVersion: доступна более новая версия Gradle).
- Все **15** текущих файлов `apps/client/{lib,test,integration_test}` побайтно
  совпадают с source snapshot
  `C:\Users\JetGame\AppData\Local\ProjectVpn\i013-builds\4538619171f24d378c168216a6bf9a1c`.
  Проверка выполнена SHA256; продуктовый код после сборки здесь не менялся.
- APK в `apps/client/build/app/outputs/flutter-apk/`, SHA256:
  - debug: `35b8b081ba0181828771a0c83991cac912c243505a12c599772d3b6693e03c41`;
  - release: `f8644d15aec0f0937a997a439ac5fe3180d177a889e545c1076ce1986f16efe7`.
  Это обычный main.dart; production signing/public release не заявлены.

Предупреждения сборки: будущая поддержка закреплённых Gradle 8.14.3,
AGP 8.13.0, Kotlin 2.2.20; embedded Kotlin mismatch; integration_test требует
NDK 28.2.13676358 при выбранном 27.0.12077973; deprecated Java/Gradle API и
experimental android.overridePathCheck. Сборки завершились успешно. Обновлять
весь toolchain или подавлять проверки в рамках приёмки I-015 не стали.

### Единственный оставшийся gate

ADB по явному пути `C:\Users\JetGame\AppData\Local\Android\Sdk\platform-tools\adb.exe`
успешно запустил daemon, но `adb devices` вернул пустой список. Физическая
проверка отображения reward history против локальной настоящей БД пока не
выполнена. Предыдущая физическая приёмка I-014 не считается этим новым тестом.
Для завершения нужен подключённый и авторизованный Android; сценарий описан
в `docs/runbooks/rewards.md`. После проверки сохранить результат и восстановить
обычное приложение. Mini App claim/retry UX остаётся I-017.

### Подготовка physical runner после подключения телефона

Добавлен `scripts/run_i015_android.ps1` и отдельный opt-in
`VPN_I015_LOCAL_ANDROID` в existing I-013 harness/test entrypoint. Сценарий
описан в runbook: настоящий claim API, replay, UI history/refresh, сверка
claim/ledger/reconciliation в disposable PostgreSQL; конфигурация 17 байт —
явно синтетическая. Обёртка восстанавливает сохранённый обычный main.dart APK.
Продуктовый API и main.dart не менялись, test-only routes не входят в production.

Проверки подготовленного кода: Ruff check/format, mypy по изменённому Python
файлу, Dart format/analyze и PowerShell parser прошли. Изменён только тестовый
Dart entrypoint; предыдущие APK и 38 Flutter unit tests относятся к прежней
версии entrypoint, а не доказывают новый physical scenario.

После сообщения владельца о включённой отладке повторный `adb devices -l`
в этой execution session по-прежнему вернул пустой список, `get-state` —
no devices/emulators found. Тест на телефоне НЕ запускался и тестовый APK
не устанавливался. Наличие USB-разрешения на стороне телефона этим не
опровергается; причина отсутствия устройства в этой сессии не установлена.
Существующая физическая приёмка остаётся открытой; новый passed receipt не создан.

Ограничения наград и отсутствие включённых production rules сохраняются.
Новые runtime tests повторно запускать только из-за старого sandbox blocker
не требуется. Код/зависимости/VPN/внешняя инфраструктура в этой сверке не менялись.

## Что изменилось

- `services/api/src/vpn_api/rewards/{contracts,routes,service}.py`: authenticated
  rules/claim, PostgreSQL clock, UTC calendar, enabled/window/eligibility,
  account-wide count/bytes cap, cooldown, original successful receipt replay.
- `db/models.py`, `migrations/versions/0009_rewards.py`: reward_rules/claims,
  FK/indexes/checks, append-only claims, ledger source reward (credit only).
  Downgrade отказывает при reward accounting. Нет seed наград и новых библиотек.
- `auth/security.py`, `auth/service.py`, `main.py`: user permission и digest
  rate buckets 120/min/account, 3000/min/global; структурированные ошибки.
- `quota/contracts.py`, `packages/contracts/openapi.json`: история source reward
  и новые API contracts. Старые quota tests учитывают новую head migration.
- `apps/client/lib/quota.dart`, `quota_screen.dart`, `orbit_theme.dart`: история
  знает источник reward, Soft Orbit tokens применены к затронутому quota экрану:
  plum, мягкая линия маршрута, круглые карточки, light/dark, четыре акцента с coral
  по умолчанию, отдельные error/warning. `main.dart`: согласованное название.
  Rewards UI относится к Mini App I-017; в обычное приложение не добавлен.
  Глобальной смены темы и Mini App нет. Native VPN код не менялся.
- ADR-027, issue/runbook, TECHNICAL_SPEC (бренд/UX) и ROADMAP.

## Фактически выполненные проверки

- `.venv\Scripts\python.exe -m ruff format --check services/api`: **70 files formatted**.
- `.venv\Scripts\python.exe -m ruff check services/api`: passed.
- `.venv\Scripts\python.exe -m mypy`: **88 source files, no issues**.
- `.venv\Scripts\python.exe scripts/export_openapi.py --check`: current.
- `pytest -q services/api/tests/test_rewards_contracts.py
  services/api/tests/test_database_config.py --tb=short`: **26 passed**.
  Это offline negative request-authority/UUID/OpenAPI и configuration checks,
  а не подтверждение транзакционного начисления.
- Закреплённый Dart formatter: новые и изменённые Dart-файлы отформатированы.
- `flutter pub get --enforce-lockfile` и отдельный `--offline --enforce-lockfile`:
  passed. Зависимости/lockfile не обновлялись.
- Flutter analyze на отдельной ASCII source copy: **No issues found**. После
  окончательного разделения интерфейсов и финальной правки: **No issues found, 7.5s**.

## История ранних неуспешных попыток (заменена успешным прогоном выше)

- Стандартный disposable PostgreSQL runner дважды превысил 60s initdb timeout.
  Диагностический повтор с timeout 300s запустил настоящий PostgreSQL. Затем
  все 76 DB-dependent tests выбранного набора упали на setup: Windows
  `10013 / Permission denied` при TCP к 127.0.0.1. Бизнес-код они не проверили.
  Итог той попытки: **14 passed, 312 deselected, 76 errors**. Это включало первые
  10 reward tests; позднее добавлены ещё три сценария concurrent clicker/overflow,
  их DB-прогон также остаётся обязательным. SQLite и mock БД не использовались.
  Успешно запущенный кластер runner остановил и удалил. После неуспешных initdb
  попыток могут оставаться generated diagnostic directories в ignored cache;
  не использовать их как готовый test cluster.
- Широкий non-postgres pytest и отдельный TestClient health run зависли в этом
  окружении и были остановлены; успешный результат им не приписывается.
- `scripts/build_flutter_client.ps1`: pub/format/analyze прошли, `flutter test`
  остановился на запрете копирования NativeAssetsManifest в sandbox path.
  Отдельный повтор из разрешённой ASCII temp copy дошёл до компилятора, затем
  failed copy output.dill → listener.dart.dill в sandbox temp; остановлен.
  Финальные два новых Flutter tests (palette contrast, reward history source) и
  существующие regression tests **не приняты**.
- До Android debug/release/unit/lint общий runner не дошёл. Отдельная попытка
  использовать предыдущий sandbox build snapshot не нашла уже удалённую папку;
  это не Android compile result. Новые APK не поставлены пользователю.
- ADB devices: daemon не смог запуститься; физический телефон не проверен.
- `git switch -c codex/i-015-rewards`: HEAD.lock permission denied. HEAD остался
  `codex/i-014-quota-history`; commit/staging не выполнялись. До работы весь
  исходный checkout был untracked, поэтому обычный git diff не отражает правки.
- uv по умолчанию не мог писать interpreter cache; для доступных Python проверок
  использовалась существующая `.venv`, без изменения зависимостей.

## История повторной попытки в ограниченной сессии

### Повторная попытка приёмки 2026-10-21

По прямому запросу владельца повторены проверки в текущей сессии. На момент этой ранней попытки ограничения
окружения сохранялись. Её результаты ниже заменены успешным локальным прогоном выше.

- Предварительная проверка обычным Python socket подтвердила Windows 10013
  при соединении с собственным loopback listener. Стандартный tempfile также
  получил PermissionError при записи. Для runner заданы TEMP/TMP внутри
  разрешённого `.cache`, без изменения исходников или ослабления проверок.
- PostgreSQL runner с `PYTEST_ADDOPTS='-q -x --tb=line -k "rewards or quota or
  migrations"'`: initdb и запуск настоящего кластера состоялись; первая fixture
  остановилась на TCP permission denied к 127.0.0.1. **312 deselected, 1 setup
  error, 2.69s**. Схема и бизнес-код не проверены. Кластер остановлен и удалён.
- `build_flutter_client.ps1` с закреплённым SDK из локальной копии: pub lock,
  Dart format (**15 files, 0 changes**) и analyze (**no issues, 8.7s**) прошли.
  Flutter tests снова остановились на forbidden copy native_assets.json →
  NativeAssetsManifest.json в sandbox build directory; exit 1.
- Отдельно запрошены Gradle **8.14.3** tasks `:app:assembleDebug`,
  `:app:testDebugUnitTest`, `:app:lintDebug`, `:app:assembleRelease` с
  `--offline --no-daemon --continue` и отдельным GRADLE_USER_HOME в `.cache`.
  Gradle запустился, но не смог подключиться к собственному single-use daemon
  на localhost. До конфигурации проекта/выполнения tasks не дошёл; exit 1.
  Это подтверждённый blocker build/unit/lint, не Android test failure.
- Повторно прошли Ruff format/check, mypy (**88 files**), OpenAPI contract check
  и **26 offline tests**. Код продукта не изменялся: новые прогоны остановились
  до проверки runtime поведения. Отчёт и ROADMAP обновлены.

Автоматические gates этой попытки впоследствии закрыты успешным локальным
прогоном выше. Открыта только физическая проверка истории наград на Android.

Ограничения: серверные лимиты не доказывают участие человека и не предотвращают
фермы аккаунтов. HTTP caller должен хранить ключ неопределённого запроса;
durable retry UX будущей Mini App пока не реализован. Экономические параметры не утверждены,
поэтому production rules отсутствуют. Никакой новый VPN/egress/payment/staging
acceptance не заявляется; внешняя инфраструктура не изменялась.

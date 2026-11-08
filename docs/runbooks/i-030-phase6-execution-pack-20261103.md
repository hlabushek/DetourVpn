# I-030 Phase 6 — пакет закрытия проверок

Дата подготовки: 2026-11-03. Это воспроизводимый план измерений и сбора
доказательств для закрытой беты. Он не означает принятие Phase 6 и не разрешает
запускать тесты на staging или production.

## Текущий снимок

- Повторный dependency scan по текущим lock hashes находится в
  `../evidence/i-030-prebeta-dependency-recheck-20261103.json`.
- OSV triage Gradle и Go от 2026-11-02 зафиксирован в соседних receipts. В APK
  `releaseRuntime` не было Gradle advisory matches, но остаются build/toolchain,
  SDK/NDK, OS/container, SBOM и license review.
- Flutter 3.47.7 SDK содержит embedded KGP 2.2.20; app-level KGP уже обновлён.
  SDK finding GHSA-r937-wjx7-w2jp требует проверки исправленного официального
  stable Flutter SDK и полного повторного build/scan.
- Go Android arm64/amd64 reachability scan повторён на 2026-11-03 с Go 1.25.14,
  govulncheck 1.7.0 и установленным NDK 27; source hashes совпали с предыдущим
  scan. Результаты и hashes — в
  `../evidence/i-030-go-android-target-scan-followup-20261103.json`. Обе цели
  имеют ноль symbol-level findings; единственная package-level запись относится
  к Windows-only stdlib API. Go vulnerability DB не обновилась с
  `2026-10-27T21:16:18Z`, поэтому семьдневный snapshot и module-level advisories
  остаются открытым review, critical gate не закрыт.
- Созданы локальные частичные CycloneDX BOM для Python и двух npm приложений;
  подробности и hashes — в `../evidence/i-030-sbom-20261103/receipt.json`.
  Это не BOM подписанного APK или server image и не полный license/security gate.
- Последняя локальная полная DB-backed suite: 1 027 passed, 13 skipped,
  0 failed; полный follow-up — в
  `../evidence/i-030-phase6-local-suite-followup-20261103.json`. Три начальных
  падения из `../evidence/i-030-phase6-local-suite-20261103.json` устранены
  только в тестовых ожиданиях: whitespace-нормализация Dart source assertion и
  актуальный isolated package в driver-test fixtures. Product/runtime и
  физический acceptance этим не меняются.
- На момент проверки Docker daemon не запущен; `syft`, `trivy`, `k6` и `locust`
  не найдены в PATH. Поэтому container SBOM и load receipt пока не создавались.

## Порядок выполнения

| Шаг | Контур | Результат | Что разрешено считать закрытым |
|---|---|---|---|
| 1. Зафиксировать candidate | локально | source ref, SHA-256 source locks и артефактов, signing receipt | Только идентичность проверенного candidate |
| 2. Зависимости и SBOM | локально, затем сканеры | BOM по каждому release target и server image, SPDX/license inventory, scanner/database versions | Только перечисленные компоненты и их конкретные версии |
| 3. Backend baseline/load | сначала отдельная локальная БД; позже — только явно разрешённый staging A | latency/error/CPU/memory/DB/queue метрики и конфиг нагрузки | Только измеренный API contour; не VPN egress и не production |
| 4. Usage/accounting lag | synthetic identities в disposable DB; позже — согласованный staging | received/ledger-visible/lag/replay/reconciliation | Только испытанный ingest путь и профиль нагрузки |
| 5. Availability/failure/rotation | непрерывные измерения и контролируемый staging drill | месячный availability ряд, detection и recovery времена | Только тестовый интервал; не обещание доступности |
| 6. Android memory/battery | физический телефон и подписанный candidate | агрегированные начало/конец, ОС, клиент, сеть и условия | Только указанное устройство/профиль |
| 7. Traffic cost | фактический provider billing и измеренные bytes | расчёт integer minor units с источниками цены | Только конкретный тариф и период |

Не объединять доказательства разных сборок, lock hashes, устройств или
конфигураций. Существующая таблица результатов —
`../evidence/i-030-phase6-measurements-template-20261102.csv`.

## 1. Release supply chain, SBOM и лицензии

1. Выбрать один подписываемый APK candidate и зафиксировать commit/source tree,
   SHA-256 APK, package/version, `apksigner verify` и receipt сборки. Текущий
   compile-only APK без подписи нельзя использовать как release SBOM subject.
2. Разделять компоненты по реальному scope: beta APK runtime; Android build/test
   toolchain; Flutter SDK и Pub; native Go target; backend Python runtime; npm
   mini-app и bot; server image и OS packages. Не объявлять BOM полного продукта
   по одному Gradle lockfile.
3. Для каждого subject сохранить CycloneDX или SPDX BOM вместе с точной версией
   генератора, его checksum, исходными lock/source hashes и target/configuration.
   Использовать maintained generator с подтверждённой поддержкой соответствующего
   формата; сначала проверить его локально на малом синтетическом fixture. Не
   добавлять generator в runtime dependencies.
4. Сверить BOM с lockfiles и реальным release artifact: package name/version,
   direct/transitive, license expression/file, source URL и исключённые scopes.
   Неизвестная/отсутствующая лицензия остаётся открытым элементом review.
5. На том же candidate повторить Python/npm/Pub/Maven/Go scanners и захватить
   scanner/database versions. Для Go отдельно повторить Android arm64/amd64
   govulncheck на актуальном официальном DB snapshot. Для Gradle отличать
   `releaseRuntimeClasspath` от debug/test/internal configurations, но для
   Critical/High в build chain записать влияние на целостность выпуска и решение
   reviewer; отсутствие runtime match не закрывает риск скомпрометированной
   сборки.
6. Для серверного image создавать BOM из собранного image digest, а не только из
   Dockerfile. Сначала нужен локальный Docker daemon или изолированная сборка
   образа. До этого image BOM — незакрытый блокер. Не запускать сборку или скан
   против B/production.

### Подготовленные частичные BOM

Артефакты созданы в `../evidence/i-030-sbom-20261103/`:

- `telegram-mini-app.cdx.json`: npm 11.12.1 `npm sbom --package-lock-only`, 67
  компонентов из lockfile `apps/telegram-mini-app/package-lock.json`.
- `telegram-bot.cdx.json`: тот же генератор и режим, 22 компонента из
  `apps/telegram-bot/package-lock.json`.
- `python-locked-all-groups.cdx.json`: CycloneDX Python 7.3.1 из
  `uv export --locked --all-groups`; 75 package components. Входной requirements
  export содержит marker-aware locked pins. Генератор отметил неполный dependency
  graph; в этом BOM нет license metadata, поэтому использовать его как inventory,
  не как закрытый license graph.
- `python-installed-env.cdx.json`: CycloneDX Python 7.3.1 из локального `.venv`,
  74 компонента и license metadata для всех 74; пустых/NOASSERTION лицензий не
  найдено. Версии совпали с lock для установленных пакетов. Это Windows Python
  3.13 environment view; он не включает
  `httpx2-jsfetch`, помеченный только для Emscripten.
- `python-pip-audit-windows.cdx.json`: дополнительный pip-audit CycloneDX scan
  view; scanner вывел `No known vulnerabilities found`. Это OSV scan result, не
  замена lock-based SBOM.

Повторяемые команды из корня checkout (npm команды запускались из каждой папки
приложения, вывод сохранялся в соответствующий evidence-файл):

```powershell
uv export --locked --all-groups --format requirements.txt --output-file docs/evidence/i-030-sbom-20261103/python-all-groups-requirements.txt
uvx --from cyclonedx-bom==7.3.1 cyclonedx-py requirements docs/evidence/i-030-sbom-20261103/python-all-groups-requirements.txt --pyproject pyproject.toml --mc-type application --spec-version 1.6 --output-reproducible --output-file docs/evidence/i-030-sbom-20261103/python-locked-all-groups.cdx.json
uvx --from cyclonedx-bom==7.3.1 cyclonedx-py environment .venv --pyproject pyproject.toml --mc-type application --spec-version 1.6 --gather-license-texts --output-reproducible --output-file docs/evidence/i-030-sbom-20261103/python-installed-env.cdx.json
uv run --frozen python -m pip_audit --require-hashes --requirement docs/evidence/i-030-sbom-20261103/python-all-groups-requirements.txt --vulnerability-service osv --format cyclonedx-json --output docs/evidence/i-030-sbom-20261103/python-pip-audit-windows.cdx.json

Push-Location apps/telegram-mini-app
npm sbom --package-lock-only --sbom-format cyclonedx --sbom-type application | Set-Content -Encoding utf8 ../../docs/evidence/i-030-sbom-20261103/telegram-mini-app.cdx.json
Pop-Location
Push-Location apps/telegram-bot
npm sbom --package-lock-only --sbom-format cyclonedx --sbom-type application | Set-Content -Encoding utf8 ../../docs/evidence/i-030-sbom-20261103/telegram-bot.cdx.json
Pop-Location
```

Все пять CycloneDX JSON прошли локальный JSON parse; версии npm/Python lock source
recorded в receipt. Оба npm BOM включают license metadata для всех перечисленных
компонентов и не имеют пустых/NOASSERTION полей по текущей локальной проверке;
это ускоряет review, но не заменяет юридическую проверку лицензий.

Непокрыто: подписанный APK и embedded Go, Gradle/AGP/KGP и Flutter SDK,
Android SDK/NDK, server image/OS packages, backend production runtime artifact и
независимая сверка лицензий. Полное supply-chain acceptance остаётся открытым.

**Критерий решения:** отдельно перечислены открытые Critical/High и их scope,
фиксированные версии, reachability/экспозиция, remediation или подписанное
обоснование риска; нет необъяснённых компонентов и лицензий; BOM и скан относятся
к одному и тому же candidate hash. Только совпадение SBOM package scope с APK
не доказывает безопасность backend/image.

## 2. API нагрузка и SLO

Текущая среда `infra/compose` помечена DEV ONLY и описана как PostgreSQL/Redis для
локальных зависимостей; она сама по себе не запускает API I-001 и не является
production-подобным стендом. Встроенный `scripts/test_with_postgres.py` — runner
для тестов, а не генератор нагрузки.

### Безопасный подготовительный этап

- Описать отдельный test profile: disposable PostgreSQL, synthetic account/device,
  отдельные signing/auth values, API на loopback, без реальных Telegram,
  provider, owner или VPN credentials.
- Перед нагрузкой зафиксировать target endpoints, read-only/write сценарии,
  размер данных, concurrency/ramp/duration, warm-up, retry policy и stop limits.
  Текущие продуктовые требования не задают эти параметры; утвердить их до запуска,
  не выдавать произвольный профиль за официальный SLO.
- Сначала прогнать короткий smoke и проверить, что base URL — loopback. Для
  нагрузочного генератора использовать поддерживаемый инструмент после
  установки/проверки pinned версии; сейчас k6/locust не обнаружены.
- Не нагружать login/OTP, платежи, reward выдачу, owner account, живой usage ledger
  или VPN tunnel без отдельного синтетического сценария и disposable данных.

### Метрики и исходные требования

На каждом запуске записывать `run_id`, scope, commit/artifact/config hashes,
UTC start/end, concurrency/ramp/duration, requests/errors, p50/p95/p99, CPU,
peak memory, queue peak и DB pool/connection peak. В CSV не сохранять токены,
пользовательские IDs, IP, URL/DNS историю или payloads.

Сопоставлять результаты с уже установленными целями из `TECHNICAL_SPEC.md`:

- API p95 < 500 ms без внешних providers;
- API availability 99.5% за месяц на ограниченном тесте;
- обнаружение отказа узла < 2 минут при исправном мониторинге;
- RPO ≤ 24 часа и RTO ≤ 2 часа в тестовом контуре.

Для месячного availability нужна непрерывная временная серия за соответствующий
период и заранее определённый probe/maintenance policy. Короткий load run не
подменяет monthly availability. Нагрузочные ошибки и отказы внешнего провайдера
нужно размечать отдельно; из p95 исключать внешние provider calls по требованиям
спецификации.

## 3. Учёт трафика и задержка ledger

1. В disposable DB создать только synthetic node/peer/device/account bindings и
   начальные значения. Использовать только тестовые UUID и стабильные
   idempotency keys.
2. Сгенерировать последовательность usage samples с известными cumulative
   counters и source timestamps. Зафиксировать время получения и время, когда
   receipt/debit стал виден в ledger.
3. Повторить каждую запись с тем же idempotency key и payload; ожидаемое число
   дополнительных списаний — 0. Изменённый payload при прежнем key проверить как
   conflict, а повтор sequence с новым key — как conflict/rejection по контракту.
4. Для каждого event вычислить `ledger_visible_at - sampled_at`; записать p50/p95/max,
   получено, отражено в ledger, пропущено, продублировано, pending и unknown/open
   receipts. В конце выполнить audit/reconciliation и сохранить aggregate-only
   результат.
5. Сопоставить с целью: при здоровой сети 95% агрегированных записей не старше
   120 секунд. До измерения определить healthy-network condition и измерительное
   окно; не переносить результат синтетического локального DB на живую node/API
   доставку.

Руководство `docs/runbooks/usage.md` подчёркивает, что local CLI — не рабочий
transport/node adapter и что live collection требует отдельной реализации и
политики retention. Нельзя использовать реальные peer payloads или проводить
эти проверки через владелецкий production/beta баланс.

## 4. Availability, node failure и rotation

- Availability измерять внешним к API synthetic probe с фиксированной семантикой
  успешного ответа и сохранёнными aggregate timestamps. Отчёт за короткий период
  не маркировать как месячный SLO.
- Failure detection измерять от контролируемого момента отказа до monitor alert;
  восстановления — от отказа до подтверждённого healthy ответного пути. Проверять
  часы/синхронизацию и исключать время подготовки оператора из service latency.
- Node rotation/failure drill выполнять только в выделенном согласованном
  staging-контуре, с заранее проверенными bounded slots/quota, наблюдателем,
  точным timeout и cleanup-планом. Не отключать A/B/production или внешние
  интерфейсы без отдельного run authorization. Текущие I-019 receipts — bounded
  evidence, а не доказательство автоматического failover или полной upstream
  независимости.
- Сохранять только aggregate `node_failure_detection_seconds`, состояние до/после,
  settlement/reconciliation и cleanup receipt; не сохранять tunnel payload, DNS,
  URL или пользовательский трафик.

## 5. Android память и батарея

Эта часть требует физического телефона. Перед визитом заранее подготовить
подписанный artifact с hash, кабель/ADB разрешение, версию Android, фиксированный
профиль экрана/яркости/радио, сеть и время прогона. Использовать split-tunnel
include-only Chrome при сценарии, где это уменьшает посторонний трафик. Не
обещать общесистемную защиту или стабильность батареи по одному устройству.

Записывать по CSV шаблону начало/конец battery level, app memory, throughput,
screen brightness, radio mode, network/profile, client/OS version и ambient
temperature, если её можно корректно измерить. Сохранять только агрегаты и
обезличенное описание профиля. До появления продуктового battery/memory budget
эти замеры — baseline/диагностика, а не pass/fail threshold.

## 6. Стоимость бесплатного трафика

- Взять фактические provider-billed bytes/GiB и цену из текущего тарифа/счёта;
  сохранить дату и источник ставки.
- Хранить расчёт в integer bytes и integer minor currency units. Явно зафиксировать
  единицы тарифа (GB или GiB), included bytes, период, округление, валюту и
  налоговую составляющую, если она применима.
- Рассчитать фактический total cost и cost per active test user только для
  измеренного периода. Экстраполяцию на бесплатного пользователя маркировать
  моделью, входными предположениями и диапазоном; не выдавать её за расход из
  реального счёта.
- Не вводить лимит цены без решения владельца. Текущая спецификация требует
  отслеживать средний/p95 трафик и cost per active user, но ценовой потолок ещё не
  задан.

## Что должно остаться для физического визита

Телефон необходим для VPN handshake/egress/revoke, DNS/IPv6 capture, split app
routing acceptance, Android memory/battery и сетевого throughput. На визите
использовать только подписанный candidate и ограниченный тестовый контур; owner
VPN probe ранее потратил 11,930,289 bytes без доказанного egress, поэтому не
повторять его без нового bounded preflight и достаточной квоты для всего окна.

Нагрузочное тестирование, полный SBOM, dependency/toolchain security review,
server image scan, месячный availability и provider billing не являются
телефонными тестами: они требуют отдельных локальных/стaging/временных данных и
доказательств. Из этого пакета ничего не запускать против B или production.

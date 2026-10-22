# Отчёт I-002 — идентичность и выбор tunnel SDK

Дата: 2026-10-18. Ветка: `codex/i-002-decisions`.

**Результат исследования: GO для review и последующего изолированного build spike;
NO-GO для выпуска/интеграции непроверенного tunnel binary.**
Все три ADR остаются Proposed. I-002 доставлена как исследовательский пакет,
но исходный gate ROADMAP «ADR accepted» ещё не выполнен.

## Решения, подготовленные к review

- ADR-001: после уточнения владельца основной вход — одноразовый код из Telegram-бота.
  Pairing, trusted Telegram Updates, лимиты и атомарное погашение заменяют прежний
  план email/OIDC. Сам способ уже выбран; технический контракт и retention ещё
  Proposed. Реальная интеграция не выполнена, независимый recovery пока не выбран.
- ADR-002: Flutter + Kotlin + non-root userspace GoBackend; exact source SDK выбран,
  описаны private-key boundary, backup/logging и план Go-only packaging.
- ADR-003: AWG Android `ff15093bf3856fea0947923474870b86efb682f0` с module
  `v3.1.20260814`, core `1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1` с обеих сторон
  будущего spike. Reference WireGuard закреплён отдельно, без скрытой замены AWG.

По вопросу открытого/закрытого клиента владелец ответил «Пока не решено».
Лицензия нашего проекта и юридическая модель распространения не выбирались за него.
Установка SDK и серверный deploy не выполнялись.

## Что обнаружено

1. Root Apache-2.0 SDK не описывает весь AAR: стандартный native build включает
   tools с GPL/LGPL/dual-license file headers. Build tool elf-cleaner рассматривается
   отдельно от shipped runtime. Это не утверждение о copyleft всего Flutter UI.
2. Makefile закрепляет Go 1.24.2, embedded module требует 1.25.0; auto-toolchain fetch
   может незаметно изменить inputs. Есть runtime patch, который нужно проверить
   с выбранной поддерживаемой версией Go. В wrapper нет distributionSha256Sum.
3. Взаимосвязь SDK → tag → core commit подтверждена metadata/исходниками; поля AWG 3.1
   найдены в Java serializer и Go UAPI. Это не binary/protocol round-trip test.
4. Для всех 26 declared Go dependencies получены root license files из versioned
   module ZIP и записаны hashes. Это не per-file clearance/SBOM выпускаемого APK:
   build-selected packages и resolved Maven graph пока не получены.
5. OSV query 26 dependencies + core дал advisory IDs для шести modules. Reachability
   не установлена, aliases не считаются отдельными уязвимостями. Например некоторые
   записи касаются SSH agent или Windows API и не доказывают проблему Android.
   Согласованный security update и target-specific govulncheck нужны до acceptance.

Известные риски не исправлялись произвольным обновлением чужого SDK/Go dependencies:
это меняло бы исследуемую baseline и потребовало бы новой сборки/матрицы.

## Файлы

| Файл | Назначение |
|---|---|
| `docs/adr/001-authentication.md` | Reviewable auth/session/recovery plan |
| `docs/adr/002-mobile-tunnel-sdk.md` | SDK boundary, точный кандидат, licensing/build gates |
| `docs/adr/003-baseline-engine.md` | Exact client/server pair, reference, capability assumptions |
| `docs/licensing-checklist.md` | Native/Go/Maven/toolchain scope, obligations и ответственные |
| `docs/compatibility-matrix.md` | SOURCE против BUILD/DEVICE, проверки и build plan |
| `docs/evidence/i-002-sources.json` | 70 pinned source-file receipts с SHA-256, gitlinks и module archive hash |
| `docs/evidence/i-002-go-dependencies.json` | 26 exact module versions, archive/license hashes и root SPDX candidates |
| `docs/evidence/i-002-go-advisories.json` | OSV результаты и примеры triage из Go vulnerability database |
| `docs/issues/I-002.md`, `docs/architecture-review.md`, `README.md` | Фактический статус исследования и навигация |

Исходники внешних библиотек скачивались только в игнорируемую `.cache/upstream-i002`
для чтения; они не добавлены в monorepo. Receipts содержат metadata/hashes/URL,
не код этих библиотек и не полный готовый SBOM. Чужие build scripts не запускались.
Реальные credentials/серверные конфиги не запрашивались и не использовались.

## Проверки 2026-10-18

Среда: Windows / CPython 3.13.1 / uv 0.11.17. Dependencies и Python API не изменены.

| Команда / проверка | Результат |
|---|---|
| `uv lock --check` | PASS, exit 0 |
| `uv run --frozen ruff format --check .` | PASS, exit 0 |
| `uv run --frozen ruff check .` | PASS, exit 0 |
| `uv run --frozen mypy` | PASS, 6 source files |
| `uv run --frozen pytest` | PASS, 9 existing backend tests |
| `uv run --frozen python scripts/export_openapi.py --check` | PASS, OpenAPI не менялся |
| JSON integrity / exact commits / cache byte hashes | PASS, 70 pinned source receipts + AWG module ZIP + 26 dependency receipts |
| OSV API querybatch | PASS, 27 modules queried; advisory records у 6 modules — это findings, не clean security result |
| Go vulnerability database triage examples | Получены affected packages/fixed version metadata, записаны с первичными URL |
| Изоляция изменений / локальные links / эвристический secret scan | Исходный research package: PASS, 14 локальных ссылок, runtime/deps/канонические документы и PGN не менялись. Последующая auth-коррекция намеренно обновляет ТЗ/roadmap по требованию владельца; проверки описаны ниже |

Для docs-only изменения новые runtime tests не добавлялись. Девять baseline tests
не проверяют OTP, OIDC, native build, counter semantics или VPN. Python audit от I-001
не выдан за сегодняшнюю проверку Go/Android. Remote GitHub Actions не запускался.

### Воспроизведение source evidence без выполнения чужого кода

Receipts используют immutable raw GitHub URLs вида
`https://raw.githubusercontent.com/<repo>/<40-character-commit>/<path>`.
Получить bytes по URL и сравнить SHA-256 с полем `sha256`; для module archives —
поле `archive_sha256` либо `module_archive.sha256`. При несовпадении остановиться,
не перезаписывать ожидаемый checksum автоматически. Не нужны API tokens.

Go `h1` из SDK go.sum записан как отдельное доказательство, но не вычислялся заново
и не проверялся независимо через sumdb. Archive SHA-256 не подменяет Go dirhash.
OSV status воспроизводится публичным querybatch для exact Go name/version;
advisories со временем меняются, snapshot отражает только дату запроса.

## Открытые gates и следующий шаг

| Gate | Осталось | Ответственный |
|---|---|---|
| B1 / toolchain | Поддерживаемый Go, bootstrap/runtime patch и native build | Отдельный локальный build spike |
| B2 / licensing | Выбранный Go-only либо полный bundle, per-file/transitive clearance и obligations | Developer + владелец |
| B3 / reproducibility | Checksummed build inputs, resolved Maven/Go packages, повторный AAR build | Developer |
| B4 / auth contract | Основной способ уже выбран: код из Telegram-бота. Остались технический pairing/Update/session контракт и retention/recovery до реализации | Developer + владелец |
| B5 / actual VPN | Разрешённый стенд, I-010/011, physical/network/usage evidence | Developer + выбранный test host/device |
| B6 / source security | Advisory triage, target reachability и исправленная согласованная revision pair | Developer до native acceptance |

Полное Android окружение не устанавливалось. Обнаружена Java 8, Go/Flutter/Gradle
не найдены в PATH; наличие Android SDK целиком не проверялось. Native/server build,
публикация APK, физические и сетевые тесты — NOT RUN. Системные настройки не менялись.

Следующая техническая задача для принятия SDK — ограниченный локальный build/security
spike по матрице. Это отдельное задание, не начатое внутри I-002. Независимый backend
можно развивать по roadmap; I-003 нужно зачесть/перепланировать по уже созданному health,
не генерировать skeleton повторно. Канонический ROADMAP не переписан без решения
по последовательности; позднее пункт авторизации уточнён по прямому указанию владельца.
I-004/auth/ledger не реализованы в этой ветке.

## Уточнение владельца: bot-code login, 2026-10-18

Основание: «авторизация должна быть через код сгенерированный в боте в тг».
ADR-001 и AUTH-01/05/06 ТЗ v0.2 синхронизированы с этим требованием. Roadmap
теперь предусматривает минимальный bot adapter уже для I-005, независимо от Mini App.
README/checklist/B4/issue отражают тот же план. Исторические receipts Authlib
оставлены как след исследования, но библиотека больше не является выбранной.

Изменились только документы. Runtime, зависимости, OpenAPI и tests сохранены;
повторный backend test run после текстовой коррекции не требуется. Проверяются
локальные ссылки, JSON receipts, отсутствие прежнего основного email/OIDC плана
и изоляция изменений. Реальная отправка сообщений, webhook registration,
production tokens и запуск бота не выполнялись.

Результат этой проверки: PASS — обновлены 9 Markdown документов, проверены 7
локальных ссылок и JSON receipts; эвристический secret scan без findings.
Runtime, dependencies, OpenAPI, tests, PGN и source receipts не изменены.

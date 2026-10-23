# Зависимости I-001

I-023 (2026-10-23): новых библиотек не добавлено. Закреплённая cryptography 50.0.2
используется также для Argon2id PHC и RFC 6238 TOTP; проверены установленные API
и реальные password/MFA/replay tests. HMAC-SHA1 применяется только внутри
стандартного upstream TOTP, не для password hashing. Admin UI — собственные
static HTML/CSS/JS без CDN, framework или npm dependencies.

Дата проверки: 2026-10-15. Direct versions закреплены в pyproject.toml,
транзитивные версии/хеши — uv.lock. Python 3.13; uv 0.11.17.
Это инженерная инвентаризация, не разрешение распространения будущей native сборки.

| Runtime dependency | Версия | License metadata | Причина |
|---|---|---|---|
| FastAPI | 0.143.2 | MIT | App factory, HTTP routing, OpenAPI |
| Pydantic | 2.14.1 | MIT | Явные response schemas |
| pydantic-settings | 2.16.0 | MIT | Типизированная env-конфигурация |
| Uvicorn | 0.54.0 | BSD-3-Clause | Локальный ASGI server, без standard extras |

Источник metadata — [PyPI](https://pypi.org/); версии получены из JSON API
`https://pypi.org/pypi/<package>/json` при подготовке каркаса. Releases проверены
установкой и тестами вместе, а не только по номеру версии.

Dev-only: pytest 9.1.1/MIT, HTTPX2 2.13.1/BSD-3-Clause, Ruff 0.17.0/MIT,
mypy 2.4.0/MIT, pre-commit 4.7.0/MIT, pip-audit 2.10.1/Apache-2.0.
pip-audit добавляет audit-only транзитивные пакеты, в runtime они не требуются.
HTTPX2 выбран после фактического deprecation warning текущего Starlette TestClient;
старый HTTPX не оставлен параллельной dev-зависимостью:
[HTTPX2 upstream](https://github.com/pydantic/httpx2).
Факт недавнего обновления не заменяет независимого аудита upstream.

В I-001 SQLAlchemy/Alembic отсутствовали. В I-004 добавлены:

| Dependency | Pinned version | License metadata | Назначение / сопровождение |
|---|---|---|---|
| SQLAlchemy | 2.1.5 | MIT | Sync ORM/engine; совместимость проверена реальным PostgreSQL |
| Alembic | 1.20.0 | MIT | Explicit migrations/autogenerate check; обновлять вместе с ORM checks |
| psycopg + psycopg-binary | 3.3.6 | LGPL-3.0-only | PostgreSQL driver; binary wheel включает runtime libraries, не считать весь bundle MIT |

Metadata проверена 2026-10-18 через PyPI JSON API. Mako/MarkupSafe/tzdata и остальные
transitive versions/hashes закреплены в uv.lock; доступные advisory checks выполнены.
Перед распространением образов нужны dependency SBOM и выполнение LGPL/включённых
libpq/crypto runtime notices/source obligations для конкретного артефакта.
Production licence clearance не выводится из локально прошедшего pip-audit.
Redis clients, Flutter/VPN/mail/payment/Telegram SDK пока не установлены.
В I-005/I-006 новых third-party библиотек нет: narrow Telegram HTTPS sender,
CSPRNG/HMAC/digests и локальный ручной клиент используют Python stdlib. Полный
Telegram SDK/криптобиблиотека для native key generation этим не подменяются.

## I-012 key confirmation (2026-10-19)

Добавлена `cryptography==50.0.2` (PyCA), Apache-2.0 OR BSD-3-Clause;
поддерживаемый upstream crypto package, Python 3.13 wheels доступны.
Прямой источник версии/лицензии: [PyPI](https://pypi.org/project/cryptography/50.0.2/).
Применение ограничено X25519 key agreement: никакого собственного curve/шифра.
Android использует уже закреплённый upstream Curve25519 и Android JCE HMAC,
новых Maven libraries нет. HMAC key confirmation описан в ADR-024 и требует
отдельного review перед публичным выпуском. Transitive `cffi==2.1.1` (MIT-0) и
`pycparser==3.11` (BSD-3-Clause) закреплены hashes в uv.lock.
Сопровождение: обновлять по upstream security advisories, после proof interop,
negative tests и device/native compatibility. Public release SBOM остаётся gate.

## Локальные образы и CI

I-008 также без новых third-party зависимостей: SQLAlchemy/psycopg/PostgreSQL уже
pinned, JSON Schema генерирует Pydantic, bounded CLI использует stdlib. SHA-256
только формирует advisory-lock identifiers, не заменяет authentication node channel.

В I-007 новых зависимостей нет. Ledger/receipts используют уже pinned
SQLAlchemy/psycopg/PostgreSQL; reconcile CLI — stdlib. Generated model tests
детерминированные, Hypothesis или иная новая test library не добавлялись.

Compose: PostgreSQL `17-bookworm` и Redis `7.2-bookworm`, exact manifest digests
в YAML. Они получены 2026-10-15 из официальных library repositories Docker Hub:
[PostgreSQL tag](https://hub.docker.com/layers/library/postgres/17-bookworm/images/),
[Redis tag](https://hub.docker.com/layers/library/redis/7.2-bookworm/images/).
Redis 7.2 upstream содержит BSD-style license:
[COPYING](https://github.com/redis/redis/blob/7.2/COPYING).
Перед реальным использованием проверить поддержку выбранной ветки, image scanning,
SBOM и лицензии полного образа; перенос на новую major/лицензию — отдельное решение.
Закрепление digest не означает отсутствие уязвимостей контейнера.

GitHub Actions checkout v6 и setup-uv v7 закреплены commit SHA, полученными из
upstream tag refs. Права workflow — contents:read, checkout не сохраняет credentials.
Удалённый запуск workflow в I-001 не выполнен; локальные результаты в verification.

## Telegram Serverless bot (2026-10-22)

Closed rollout operator tools (2026-10-23): uv 0.11.17, upstream Astral,
MIT/Apache-2.0, Linux asset SHA-256 pinned in `infra/closed/install-b.sh` from
GitHub release metadata. Python 3.13.12, PSF licence, maintained 3.13 branch,
downloaded through the pinned uv distribution catalogue. Certbot 5.8.0,
Apache-2.0, maintained EFF project, isolated venv; full Linux Python 3.13
dependency resolution and artifact hashes in `infra/closed/certbot-requirements.txt`.
Docker/Compose/Caddy use signed Ubuntu 24.04 repositories, exact package versions
in installer and actual dpkg versions in private rollout baseline. Caddy Apache-2.0,
Docker/Compose Apache-2.0; review bundled dependencies before public release.
OS security updates require deliberate version review; pins are not vulnerability
clearance. Renewal expiry check currently fails the systemd unit below 48h;
external alert delivery remains I-027, not implemented by this timer.

`apps/telegram-bot`: `@tgcloud/cli==0.2.0`, MIT, dev/operator tooling only.
Namespace/package указаны в [официальной документации](https://core.telegram.org/bots/serverless).
Reviewed package metadata/source и SDK reference bundled с CLI; registry integrity
проверен. Версии/integrities 22 dependency packages закреплены package-lock.json,
install выполнен с --ignore-scripts; npm audit сообщил 0 vulnerabilities.
Это молодой early-access CLI; runtime/secrets/deployment API не считать стабильным
контрактом. Нужна проверка фактического early-access окружения перед deploy.
Runtime dependencies не добавлены: Telegram предоставляет `sdk`/`sdk/db`.
Node 24.15.0 используется локально; node:sqlite только TEST ONLY SQLite harness.
Никакой npm library, CLI, loader или fake adapter не входит в tgcloud module space.

## I-011 Android engineering spike (2026-10-19)

Локально собран отдельный GoBackend-only SDK из ревизии ADR-002; новые Python
libraries не добавлялись. Go core тот же, что в I-010, Go 1.25.14 и NDK 26.1.
Java SDK использует AndroidX annotation 1.7.1/collection 1.4.0 (Apache-2.0),
jsr305 3.0.2 только compile-time; JUnit 4.13.2 (EPL-1.0) только tests.
Это существующие upstream SDK dependencies. AGP 8.13.0 и Gradle 8.13 — build
tools (Apache-2.0), не библиотеки VPN-протокола. Gradle Wrapper LICENSE сохранён.

Версии Maven/transitive dependencies закреплены `apps/android/*/gradle.lockfile`,
SHA-256 — `apps/android/gradle/verification-metadata.xml`; final build выполнен в
strict verification mode. Первичное заполнение metadata — trust-on-first-use из
Google Maven/Maven Central, не независимая подпись артефактов. До публичного
выпуска нужен дополнительный supply-chain review полного build plugin closure.

Source/runtime changes перечислены в patch и bundled MODIFICATIONS; root backend,
RootShell/ToolsInstaller и tools executables не входят в AAR/APK. В assets включены
SDK COPYING, Go LICENSE и root licence/notice files доступного Go module graph.
Это **не завершённый per-file licence inventory/SBOM** и не решение о лицензии
нашего приложения. ADR-002 release gate открыт. Сопровождение — разработчик
проекта: обновления SDK/core принимаются согласованной парой после compatibility
и device tests, а не автоматически по latest.

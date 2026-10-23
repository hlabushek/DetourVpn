# I-022 — локальная проверка 2026-10-23

Настоящий PostgreSQL 17, синтетические аккаунты/время. Targeted quota/migrations/
closed набор: 94 passed / 103.09s. Первые subscription tests: 14 passed / 18.93s.
Расширенные subscription/backfill/v2-gate/encrypted restore: 17 passed / 36.07s.
Дополненный explicit demo catalog: 1 passed / 4.77s; точные 99/199/29 RUB minor
units и 100/300/10 GiB, повтор bootstrap не создаёт новых revisions.

Полная backend/infra регрессия: **578 passed, 1 failed, 4 skipped**, 517.45s.
Единственный отказ — старое ожидание downgrade поверх nonempty ledger; ADR-035
теперь явно запрещает потерю funding provenance. Тест обновлён на отказ с
сохранением схемы/баланса. Следующий targeted run: 39 passed, 1 failure из-за
запуска до загрузки нового import DBAPIError; после import isolated test:
**1 passed / 2.35s**. Полный набор после этой тестовой правки не повторялся.
Фактические пропуски: I-012/I-013/I-019 opt-in physical/staging, Mini App browser.

Ruff check/format passed; mypy 119 files passed; OpenAPI/usage contracts current.
Добавлен запрет libpq dbname option injection по той же границе, что backup CLI.
Отрицательные URL cases проверены targeted вместе с subscription/usage tests.

Flutter: 50 tests passed. Прямой анализатор в OneDrive упал с LSP truncated JSON;
штатная копия вне OneDrive: analyze no issues, format unchanged, 50 tests passed.
Debug APK build, native testDebugUnitTest/lintDebug: BUILD SUCCESSFUL (121 tasks).
Release APK build passed; SHA256:
E7A442E34DF4B152682259C7D8E591F48CE18F6028FD5F070CE60B8892989B9D.
APK не настроен на постоянный API и не является повседневным VPN release.
Existing Gradle/Kotlin/NDK warnings остаются, toolchain не обновлялся вне issue.

ADB readiness повторно проверена после сообщения владельца: Pixel 8 Pro,
device authorized, API 37. APK не устанавливался, туннель/egress/DNS/IPv6/
network transitions не тестировались. Удалённые services не менялись.

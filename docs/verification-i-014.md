# I-014 — проверка квоты и истории

Дата: 2026-10-21. Ветка: `codex/i-014-quota-history`.
Архитектурное решение: ADR-026. Issue: `docs/issues/I-014.md`.

Добавлен экран «Трафик и история» из главного экрана. Он показывает точные
начисления, списания, резерв и доступный остаток, историю с серверной ревизией,
страницы по 20 записей и повтор загрузки. Максимум 500 записей в памяти явно
обозначен. Время снимка и устаревание видимы; после logout история очищается.
Частные ключи и туннельные контракты не изменены. API/миграций/зависимостей нет.

## Фактические результаты

- `ruff format --check`, Ruff и mypy изменённого Python harness: passed.
- Финальный Dart formatter: 13 files, 0 changes; Flutter analyze: no issues;
  Flutter test: **36 passed**, включая quota/history и regression I-013.
- Финальный Android build runner завершился успешно: debug APK (60.0s),
  unit/lint Gradle (BUILD SUCCESSFUL, 1m10s), unsigned release APK (38.8s).
  XML unit reports: **10 tests, 0 failures, 0 errors**.
- APK archive/aapt2 inspection: обычные main.dart debug/release, AWG core для
  arm64-v8a/x86_64; test routes/stage/fake-token markers отсутствуют. Release
  не содержит native integration_test plugin/debug network policy, cleartext
  и backup выключены. Это ограниченный build audit, не полный security review.
  SHA256 и проверки: `docs/evidence/i-014-apk-audit.json` (`passed=true`).
- После test entrypoint обычный debug APK установлен обратно на телефон.
  API origin не задан: обычный APK показывает setup screen; локальный тестовый
  API остановлен. Для рабочего API нужна отдельная конфигурация сборки по
  `docs/runbooks/flutter-client.md`, никаких дополнительных VPS изменений I-014
  не требует.
- PostgreSQL quota/history regression: **69 passed, 323 deselected**, 70.44s.
  Включает реальные API/ledger, отрицательные/replay/concurrency проверки.
- Physical `run_i014_android.ps1`: **1 passed, 391 deselected**, 39.19s.
  Pixel 8 Pro: exact amount >2^53, 20+5 history entries, refresh/idempotent seed;
  API admission-closed/disconnect/settlement regression passed. Receipt
  `docs/evidence/i-014-local-android.json`: `passed=true`, `live_staging=false`,
  `quota_database_consistent=true`, `quota_ui_exact_and_paginated=true`.
- Cleanup: phone stopped/reverse removed, API port 18766 has no LISTEN socket;
  disposable PostgreSQL stopped and generated cluster removed.
- Светлая/тёмная quota views отрендерены из реальных widgets на явно fake data,
  обе PNG проверены визуально; large text widget test проверяет scroll/overflow.
- PowerShell parser: оба изменённых runner без syntax errors.

До начала работ все существующие файлы checkout были untracked. Создана отдельная
ветка I-014, массовый initial commit/staging исходного проекта не выполнялся.

## Воспроизведение

```powershell
.\scripts\build_flutter_client.ps1
# Ровно один разблокированный Android с USB debugging:
.\scripts\run_i014_android.ps1
# После integration test вернуть обычный main.dart APK:
.\scripts\build_flutter_client.ps1

uv run --frozen ruff format --check services/api/tests/test_i013_android.py
uv run --frozen ruff check services/api/tests/test_i013_android.py
uv run --frozen mypy services/api/tests/test_i013_android.py
$env:PYTEST_ADDOPTS = '-q --tb=short -k "quota or quota_history"'
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Build runner использует свежий source snapshot вне OneDrive и включает Dart
format/analyze/tests, Android debug build, `:app:testDebugUnitTest`,
`:app:lintDebug`, unsigned release build, lockfile check. Tests включают int64,
резерв, ошибки/повторы, cursor/revision, двойной запрос, logout/dispose race,
лимит памяти и крупный шрифт; fakes помечены TEST ONLY.

Physical runner не принимает staging flags. Он использует локальный I-013 harness:
FastAPI + disposable PostgreSQL + ADB reverse. Telegram delivery — TEST FAKE ONLY.
24 синтетических начисления с повторяемыми idempotency keys добавляются к initial
grant; одно больше 2^53. На телефоне проверяются exact UI amount, 20+5 записей,
повтор seed без двойного начисления и refresh. Python сверяет доступную сумму с
реальными ledger rows в БД. Затем admission-closed/disconnect/settlement regression
выполняется без внешнего endpoint. Receipt: `docs/evidence/i-014-local-android.json`.
Harness останавливает приложение, снимает temporary VPN app-op, убирает reverse,
закрывает API; disposable PostgreSQL runner останавливает и удаляет cluster.

## Ограничения

Тестовые начисления не являются настоящим расходом VPN. Staging не менялся;
повторная проверка egress/DNS/IPv6 не выполнялась. Резерв — состояние quota GET,
не непрерывный счётчик; после settlement может потребоваться обновление.
История загружается в RAM и ограничена 500 последними записями. Public release,
signing и исходные licence/SBOM/security gates не закрыты.

На первых локальных запусках исправлены ошибка Dart collection-if и три
предупреждения inference в test-only empty lists. Проверки не обходились.
Первый physical harness требовал ожидания новой route; затем локализован сбой
test-only scroll на `quota_scroll` (строка теста сохранена без raw widget data).
Прокрутка теперь явно выбирает history Scrollable и ждёт завершения перехода,
когда home и history временно присутствуют одновременно. Параллельный запуск
Flutter HTTP unit tests и physical API один раз дал четыре `address in use` на
18766: эти наборы используют фиксированный debug-only port и выполняются
последовательно. Cleanup API/ADB/БД подтверждался и при неуспешных попытках.
Известные предупреждения закреплённого Android tooling о будущих Gradle/AGP/Kotlin
версиях и NDK integration_test сохранены; миграция tooling вне I-014.

Следующий issue: I-015 — clicker + daily reward, server-side no double credit.

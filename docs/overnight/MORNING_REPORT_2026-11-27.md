# Утренний отчёт, 2026-11-27 (ветка `claude/connection-reliability`)

Ночная работа шла только в папке `.claude/worktrees/fixes-before-beta-0eeeba`, от коммита
`d6f9cb6`. **Ничего не отправлено в origin (push не делался), ничего не развёрнуто, к серверам и
VPN не подключались, `run_overnight_build.ps1` не запускался.** Папку с веткой
`codex/i025-android-windows` не трогали. Файл плана `NIGHT_PLAN_2026-11-27.md` оставлен
неотслеживаемым, как был.

## Шаги

| Шаг | Статус | Коммит | Результат |
|---|---|---|---|
| A1 Базовые тесты | готово | `f27bfe3` | `docs/overnight/A1_TEST_BASELINE.md`, скрипт `docs/overnight/A1_java_substitute.sh` |
| A2 Ревью ADR-144..154 | готово (только рекомендации, кода не меняли) | `261602e` | `docs/overnight/A2_REVIEW_ADR144_154.md` |
| A3 Маршрутизация проверки (`bind_refused`) | готово, нужно решение владельца | `afb2b2c` | `docs/adr/198-health-probe-routing.md` + комментарий в `TunnelProbeRunner.java` |
| A4 Порядок перезапуска и `setUnderlyingNetworks` | готово (статический JVM-тест + пункт для телефона) | `30778e9` | тест в `ConnectionWiringTest.java`; пункт 4.2 в A5 |
| A5 Чек-лист для телефона, фазы 1-6 | готово | `a958e34` | `docs/overnight/A5_PHONE_CHECKLIST.md` |
| A6 Нужен ли `run_overnight_build.ps1` | готово (только чтение) | `741bf20` | `docs/overnight/A6_OVERNIGHT_BUILD_STATUS.md` |
| A7 Проверка окна 900/3600 на сервере | готово (runbook, команды не запускались) | `ecd15e4` | `docs/overnight/A7_WINDOW_3600_CHECK.md` |
| A8 Раздел «Current state» | готово | `cb31178` (+ уточнение в коммите этого отчёта) | `docs/git-workflow.md` |
| B1 Автовыбор узла | готово (чистая логика, не подключена к экранам) | `e783c86` | `apps/client/lib/node_choice.dart`, тесты, `docs/adr/199-automatic-node-choice.md` |
| B2 Сводка здоровья для владельца | готово (не развёрнуто) | `a4592cd` | `GET /api/admin/v1/health-summary`, тесты, OpenAPI, `docs/adr/200-owner-health-aggregates.md` |

## Главное

- **A2:** явных ошибок, которые можно доказать unit-тестом, не найдено. Самое важное: проверка
  через туннель (ADR-148), скорее всего, на телефоне никогда не сработает (`bind_refused`), сейчас
  она просто выключена. Две мелочи низкой важности описаны с файлом и строкой.
- **A3 (ADR-198):** рекомендация — для беты оставить проверку выключенной; сначала проверить на
  телефоне пункт 5.2; если `bind_refused` подтвердится — сделать «пассивный» шаг по остановке
  трафика (вариант 5), а не пускать трафик приложения в туннель.
- **A6:** скрипт больше не нужен: дерево `c6` уже подготовлено и совпадает с патчем, а
  `beta37_release_console.ps1` делает то же самое и проверяет чистоту дерева.
- **A1:** HANDOVER называет два теста, которые нужно запускать из `apps/client/android/app`;
  на самом деле их три (ещё `ConnectionWiringTest`).

## Команды проверки и реальные результаты

| Команда | Результат |
|---|---|
| `flutter test --no-pub` в `apps/client` (до B1) | 431 passed |
| `flutter test --no-pub` в `apps/client` (после B1) | **441 passed** (10 новых) |
| `flutter test --no-pub test/node_choice_test.dart` | 10 passed (первый запуск упал на компиляции: не хватало импорта `panelServerSource`; исправлено) |
| `flutter analyze` на ASCII-копии (до и после B1) | 1 ожидаемое предупреждение `assets/orbit/` (exit 1 только из-за него) |
| `dart format --set-exit-if-changed lib test` на ASCII-копии (A1) | 0 изменений; новые файлы B1 отформатированы |
| `bash docs/overnight/A1_java_substitute.sh` (A1) | javac OK; 274 + 11 passed (первый запуск: 4 падения `NoSuchFileException`, тест запускался не из той папки; исправлено в скрипте) |
| то же после A3 (только комментарий) | 274 + 11 passed |
| то же после A4 | **274 + 12 passed** |
| `uv run --frozen python scripts/export_openapi.py --check` | до: current; после B2: re-export, затем current; diff только новый путь и схема |
| `python scripts/test_with_postgres.py --postgres-bin <portable pgsql> -- services/api/tests/test_admin_health.py` | **5 passed** (первые два запуска упали на ограничениях БД в моих тестовых данных; данные исправлены) |
| то же для `test_admin_health.py test_admin.py test_health.py` | **49 passed** |
| `ruff check`, `ruff format --check`, `mypy` на изменённых файлах B2 | без замечаний |
| `uv run --frozen python scripts/scan_secrets.py` | findings=0 |

## Не проверено (явно)

- **Ничего не проверено на телефоне.** Все фазы 1-6, порядок `restartInPlace` →
  `setUnderlyingNetworks` (A4 проверяет только порядок строк в исходнике), `bind_refused`.
- Нет сборки Gradle, `testDebugUnitTest`, lint, слияния манифеста; Java-проверка — это замена
  через javac + JUnit, не Gradle.
- A7: состояние окна (900 или 3600) на API и узлах **неизвестно**; команды не запускались.
- B1 не подключён ни к одному экрану; поведение приложения не изменилось.
- B2 не развёрнут, не проверен на реальной или staging-базе, панель администратора его не вызывает.
- В A2 не прочитаны построчно: `DozeSurvival`, `BackstopJobService`, `BootReceiver`,
  `RestoreTrigger`, `RecoveryCompanionService`, `ConnectionEventLog`, большая часть изменений
  `window_connection.dart`.
- Полный набор pytest не запускался (только файлы выше).

## Решения владельца

1. **ADR-198:** оставить проверку через туннель выключенной для беты (рекомендация) и какой
   вариант брать после проверки на телефоне (рекомендация: вариант 5; не вариант 2).
2. **A7:** выполнить команды только для чтения; если где-то 900 — развернуть 3600 (сначала
   узлы, потом API) или собрать клиент с `-WindowSeconds 900`. До этого не раздавать beta.37.
3. **A6:** пометить `run_overnight_build.ps1` как устаревший или удалить отдельным коммитом
   (вместе с упоминаниями в HANDOVER, ADR-150 и runbook).
4. **A2 п.2:** должен ли интервал 15 с после перезапуска (`lastRestart`) сбрасываться для нового
   окна. **A2 п.3:** принять ли округление до секунды в «рукопожатии после смены сети».
5. **ADR-199:** где использовать автовыбор (вариант «Авто» в выборе региона, вместо правила
   отказоустойчивости ADR-148, или для первого подключения) и откуда брать счётчик неудач.
6. **ADR-200:** развернуть ли эндпоинт и добавить ли его в панель администратора.
7. **Телефон:** пройти `A5_PHONE_CHECKLIST.md` (особенно 4.2 и 5.2).
8. **Push:** ветка опережает origin на 11 коммитов (включая этот отчёт); отправлять ли её.

## Изменённые файлы (всего)

`docs/overnight/` (A1, A1-скрипт, A2, A5, A6, A7, этот отчёт), `docs/adr/198-…`, `199-…`,
`200-…`, `docs/git-workflow.md` (только раздел «Current state»),
`apps/client/android/app/src/main/java/org/example/vpnpoc/TunnelProbeRunner.java` (комментарий),
`apps/client/android/app/src/test/java/org/example/vpnpoc/ConnectionWiringTest.java`,
`apps/client/lib/node_choice.dart`, `apps/client/test/node_choice_test.dart`,
`services/api/src/vpn_api/admin.py`, `services/api/tests/test_admin_health.py`,
`packages/contracts/openapi.json`.

**Итог: ничего не отправлено в origin и ничего не развёрнуто.**

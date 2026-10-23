# Android Soft Orbit — UI/UX

Дата: 2026-10-22. Отдельная UI-задача по запросу владельца после локальной I-019.
Референс: предоставленный владельцем Soft Orbit, Android-композиция.

## Изменения

- Единая Soft Orbit theme: тёмный фон, сиреневые акценты, коралловая кнопка,
  светлая тема, градиентные карточки и декоративная орбита, нарисованная Flutter.
- Главная: статус из существующего ConnectionController, одна основная кнопка
  connect/cancel/disconnect, компактный реальный баланс и видимый test scope.
- Выбор сервера вынесен в scrollable bottom sheet. Сохранены причины отказа,
  время обновления, stale/error/empty/loading и запрет смены при живом intent.
- Нижняя навигация VPN / Трафик / Профиль. Контроллер не пересоздаётся при смене
  вкладки или темы; переходы не выполняют connect/disconnect.
- Трафик: компактные десятичные единицы только для представления, точные integer
  bytes и история сохранены; пояснения вынесены в «Как считается трафик».
- Профиль: информация о RAM-only сессии/ключе, переключение темы, ограничения
  тестовой версии, выход через существующий native-stop-first flow.
- Ошибки, remote revoke pending и явный recovery с предупреждением о полном
  списании резерва сохранены. Ошибка native stop оставляет действие отключения.

## Файлы

`apps/client/lib/main.dart`, `connection_screen.dart`, `orbit_theme.dart`,
`orbit_widgets.dart`, `quota_screen.dart`; widget/node-failover/UI-preview tests.
README клиента, этот отчёт и примечание ROADMAP.

## Проверки

Воспроизведение: `./scripts/build_flutter_client.ps1` из корня Windows checkout.
Runner создаёт ASCII source snapshot вне OneDrive, использует закреплённые
Flutter/dependencies, выполняет format/analyze/test, debug APK, Android native
unit tests/lint, unsigned release APK; результаты копируются в
`apps/client/build/app/outputs/flutter-apk/`.

Финальный runner завершился успешно: format без изменений, analyze без замечаний
(5.1s), **47 Flutter tests passed**, **10 Android native unit tests, 0 failures,
0 errors**, lintDebug passed, debug APK и unsigned release APK (42.9 MB) собраны.
Gradle native tests/lint: BUILD SUCCESSFUL (41s); release: 33.5s.
`git diff --check` прошёл. Хеши сборок и источников —
`docs/evidence/android-soft-orbit-local.json`.

После первой визуальной проверки уточнены размеры/цвета и раскрываемые пояснения.
Промежуточный прогон остановился на UI-test: при text scale 2 он нажимал элемент
вне viewport, затем ожидал ещё не построенные lazy items панели. Тест исправлен:
отдельная прокрутка главной и bottom sheet, text scale применяется к обоим.
Сначала прошёл этот targeted test, затем весь финальный runner выше.

Существующие предупреждения Gradle/AGP/Kotlin/NDK и upstream deprecated API
сохраняются; обновление toolchain этой задачей не выполнялось.

Просмотрены тёмные/светлые previews главной, трафика и профиля, экран входа и
отключённое состояние. Общая иллюстрация:
`docs/evidence/android-soft-orbit-preview.png` — TEST ONLY данные/статус VPN.

Тесты: навигация и смена темы при работающем TEST ONLY native adapter, безопасный
logout, главный disconnect, narrow 320px / text scale 2 / обе темы, recovery
warning, stale quota и точные большие числа. UI previews рендерят реальные widgets
с явно тестовыми API/node/native данными. Это не доказательство реального VPN.

## Безопасность и ограничения

Backend/API/OpenAPI, VPN state machine, native engine, ключи, хранение сессии,
биллинг, Telegram/Mini App и VPS не изменены. Новых зависимостей нет. Страны,
латентность, личные данные и баланс не выдумываются ради референса. Орбита
декоративная и не изображает измеренную сеть. Дробные единицы вычисляются
целочисленно; дробь в progress indicator используется только для визуализации.

По умолчанию тёмная тема; выбор темы остаётся в памяти до завершения процесса.
RAM-only login и повторный вход после завершения процесса остаются ограничением
I-013; безопасное постоянное хранение здесь не реализовано. Профиль не добавляет
backend account/device management. Android reward UI не добавлен этой UI-задачей.

APK без `VPN_API_ORIGIN` показывает явный экран настройки. Чтобы проверить
реальный вход/туннель на телефоне, нужен существующий согласованный API/staging
контур и runner I-013 с корректным origin. Секреты в dart-define не передавать.
Установка на физический телефон и live tunnel acceptance в этой задаче не
выполнялись. Следующий UI-шаг — просмотр сборки владельцем на Pixel; следующий
инфраструктурный gate остаётся двухузловой staging drill I-019.

# Telegram Serverless bot — подготовка и запуск

## Текущий scope

Обновление 2026-10-22: после явного разрешения владельца menu deployment выполнен
для @DetourVpnBot (revision 2); миграции применены, 6 private-chat commands
зарегистрированы, hosted V8/SQLite probe прошёл. Инструкции ниже описывают также
первоначальную подготовку. Auth relay и публичные ссылки остаются открытыми.

Если CLI 0.2.0 падает **после** списка загруженных modules при snapshot write,
не повторять push: сначала проверить облачную revision/state и выполнить
`npx --no-install tgcloud fetch`. У нас облако приняло deployment, а fetch
восстановил local snapshot. Не редактировать `.tgcloud/` вручную.

Реализован native JS bot navigation. Публичные ссылки не предоставлены. OTP/auth relay
закрыт до подтверждения secret storage/HTTPS trust contract — ADR-032.
**Не переключать webhook бота, который используется Android login.** Отдельная
проверка меню возможна только на явно выбранном staging bot; создавать его
автоматически и считать любой существующий бот тестовым нельзя.

Исходники: `apps/telegram-bot`. Runtime: только `tgcloud/`, не весь репозиторий.
Mini App source/hosting и control-plane API этим проектом не публикуются.

## Локальный preflight

Из `apps/telegram-bot`:

```powershell
npm ci --ignore-scripts
npm run check
npm test
```

Тесты не требуют токена, не вызывают Telegram и не используют production DB.
Проверить `tgcloud/lib/config.js`: только подтверждённые публичные HTTPS URLs,
без credentials. Нет ссылки — оставить пустой строкой. Настроенные ссылки не
должны содержать одноразовые download tokens, login attempts или персональные
данные. Для Mini App нужен HTTPS origin, где уже работает соответствующий API;
его перенос на `tgcloud.ai` здесь не выполнен: текущий Mini App использует
same-origin FastAPI routes и не заработает от одного копирования static files.

## Привязка платформы — ручной шаг после выбора разрешённого staging bot

В @BotFather → выбранный бот → Serverless. Само включение меняет маршрут
Updates: сначала установить, что этот бот не используется для текущего входа.

У платформы CLI access token отличается от Bot API token. Вводить его только
в локальном интерактивном терминале, **не в чат**:

```powershell
npx --no-install tgcloud login
```

`.tgcloud/` игнорируется Git; не открывать/копировать его содержимое в чат,
evidence, архивы и screenshots. Не включать TGCLOUD_DEBUG: CLI умеет печатать
request bodies и module source. `npm run status` и `npm run diff` предусмотрены
для review; по проверенному source CLI `status` может дополнительно запросить
webhook status, если проект привязан. Это не полностью offline при token.

Перед любым push: посмотреть handlers/schema diff и текущие cloud modules.
Полный `push` удаляет remote modules, отсутствующие локально; не запускать его
на неизвестном чужом/действующем проекте. Сначала установить baseline/rollback.

## Beta-публикация 2026-10-26

Отдельный Serverless app для `@uysdgyfudf_bot` связан через новый CLI Access
token; `status` подтвердил revision 0 и отсутствие модулей. Основной
`@DetourVpnBot` не затрагивался. Beta push создал 8 модулей, `migrate --safe`
добавил только две таблицы operational dedupe/rate-limit. Итог: revision 1,
webhook in sync, рабочая папка clean. Hosted `runtimeProbe` не обнаружил env/
secret интерфейса. Receipt:
`../evidence/i-030-beta-serverless-deployment-20261026.json`.

Реальный user chat, `/start <request_id>`, Mini App initData, OTP и Android
verification остаются непроверенными. Не считать публикацию Serverless готовым
входом или доставкой OTP.

## Публикация для другого разрешённого окружения

Сначала review cloud baseline и module diff. Не публиковать в неизвестный или
используемый бот.

```powershell
npx --no-install tgcloud push
npx --no-install tgcloud migrate --safe
```

Для beta уже приняты только `bot_menu_updates`, `bot_menu_limits` и их expiry
indexes. Для остальных окружений сверять миграцию отдельно. Не использовать `--force`, `migrate --yes`, автоматическое принятие
DROP или `--drop-pending`. Возможна краткая недоступность между push и migrate:
handler fail closed при отсутствии таблиц. Не публиковать для реальной аудитории
до миграции и platform QA.

`tgcloud run` исполняет код в облаке и может отправить сообщение/изменить DB,
хотя не публикует handler. Это не локальный тест. Использовать только собственный
разрешённый test chat и свежий Update shape с настоящим user ID, не выдуманный
посторонний chat_id. Payload/ctx не печатать при наличии секретов/личных данных.

После согласованного развёртывания проверить `/start`, все разделы/возвраты,
повтор Update, кнопки из старого меню, rate limits, callback spinner и DB cleanup
в hosted runtime. Локальные SQLite tests этого не заменяют. Нативная тема чата
управляется клиентом Telegram; Soft Orbit остаётся стилем Android/Mini App.

## Команды и оформление BotFather

Через управление выбранным ботом установить команды из
`apps/telegram-bot/tgcloud/lib/menu.js` (`commands`). Данные для ручного ввода:

```text
start - Главное меню DetourVpn
cabinet - Открыть кабинет
android - Приложение Android
connect - Как подключиться
help - Помощь
privacy - Данные и приватность
```

Описание: «DetourVpn — Android-приложение и кабинет трафика. Тестовая версия.
Инструкции, доступные бонусы и помощь — в меню бота».
Не рекламировать завершённую VPN fleet, безлимит, universal bypass или платежи.
Только после появления публичной Mini App ссылки настраивать menu button.

## Auth relay gate

Владелец и Codex проверили Serverless UI BotFather 2026-10-25. На странице
показаны CLI Access, Handlers, Endpoints, Library, Database и Mini App Files;
отдельного Secrets/env раздела не видно. Runtime probe в endpoint Console также
не обнаружил известных env/secret globals, context fields или SDK exports.
Официальная документация описывает вызов endpoint из Mini App с платформенно
проверенным `initData`, но не обещает общий secret injection или доставку `/start`
Updates к endpoint. Это не исключает скрытую/непривязанную настройку или
недокументированную возможность. Не помещать bot token в endpoint source,
database, URL или логи. Для текущей OTP схемы FastAPI должен получать token через
секретное хранилище отдельной beta-среды и сам отправлять код через Bot API.

Во время просмотра CLI Access его скрытое значение появилось в accessibility
output. Оно не копировалось и не использовалось; владелец отложил ротацию.
Считать credential потенциально раскрытым, не использовать и не открывать его
повторно; вернуться к решению о ротации до будущих cloud CLI операций.
Подробности без значения токена:
`docs/evidence/i-026-serverless-runtime-probe.json`.

После выяснения: отдельный reviewed relay контракт к существующему HTTPS auth
API, scoped credential (никаких admin/session keys), сохранение update_id и
private-message checks, replay/rate/negative tests и проверка реального Android
login. Не доверять внешнему `user_id`, unsigned JSON или URL с secret query.
Не хранить shared secret в runtime source/SQLite только ради быстрого запуска.

## Retention, rollback и удаление

TTL update ID 24h, user buckets до 2min; физический cleanup на следующем
допустимом Update, поэтому после простоя записи живут дольше. Код не пишет
сообщения и не имеет console logging, но Telegram provider logs вне его контроля.
До реальных пользователей нужны privacy policy и согласование retention.

Перед заменой existing handlers сохранить их reviewed baseline вне публичного
репозитория. Rollback handler — восстановление прежних modules; webhook route
проверить отдельно. Schema rollback не удаляет данные автоматически: для удаления
table/index используется reviewed deprecation/migration по правилам Telegram.
При окончательном удалении staging bot отдельно согласовать очистку operational
DB; не удалять неизвестные таблицы или чужие данные.

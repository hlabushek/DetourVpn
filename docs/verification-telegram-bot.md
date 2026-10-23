# Telegram Serverless bot — фактическая проверка

## Обновление: согласованная публикация 2026-10-22

После явного «давай» владельца опубликован бот **@DetourVpnBot**. Baseline:
revision 0, ни одного cloud module, static hosting нет, pending updates 0.
Согласованный scope: menu handlers + две operational tables, без OTP relay.

- Опубликованы 7 модулей. После записи подтверждённого username — **revision 2**.
  GET cloud state подтвердил побайтовое совпадение всех 7 исходников с checkout.
- Созданы только `bot_menu_limits`/`bot_menu_updates`. Hosted SQLite inspection
  подтвердил таблицы, оба expiry index и автоматический PK index.
- Pending migrations: **0**, webhook **in sync**, allowed updates:
  `message`, `callback_query`. Точный webhook URL/credentials в evidence не записаны.
- В реальном Telegram V8 runtime импортированы handler dependencies; недоверенные
  пустые message/callback отвергнуты, меню формируется. Native SQLite admit с
  синтетическим operational ID вернул `accepted`, повтор — `duplicate`.
  Проверочные записи удалены; реальные сообщения при probe не отправлялись.
- Native `api.getMe` подтвердил username. В scope `all_private_chats` установлены
  шесть команд; read-back совпал с `commands`, до настройки список был пустым.
- Повторные local check / **44 tests passed** после config change.

Первый `tgcloud push` принял deployment, но CLI 0.2.0 упал при записи локального
snapshot (`ERR_INVALID_ARG_TYPE`, boolean false вместо source text). Сначала
проверено облако: revision 1 уже существовала; повторный deploy не делался.
`tgcloud fetch` успешно восстановил local snapshot. Последующее изменение только
`lib/config` применено через штатный batchDeploy из CLI с expected_revision=1,
полным сохранением manifest и без static changes; после GET verification выполнен
обычный `tgcloud fetch`. Dependency source не исправлялся. Это выявленная проблема
CLI/server response compatibility, а не отказ загрузки кода.

Hosted receipt: `docs/evidence/telegram-bot-deployment.json`.
**Доставка `/start` в настоящий чат пользователя пока не проверена**: владелец
должен открыть `https://t.me/DetourVpnBot` и нажать Start. OTP, Mini App/download/
support links по-прежнему не подключены. Полная auth integration не завершена.

## Исторический локальный pre-deployment отчёт

Дата: 2026-10-22. Scope: назначенная владельцем отдельная задача Telegram bot UI.
ADR-032, `apps/telegram-bot`.

## Реализовано

Native message/callback handlers, приветствие и разделы кабинет/Android/
подключение/помощь/данные. Inline back navigation и edit существующего сообщения,
callback acknowledgement. Публичные ссылки configurable, по умолчанию пустые.
Private-chat checks, контекст Update, freshness для messages, deny forwarded/
business/via-bot/group updates, durable update claim, fixed-window user/global
limiter, operational TTL/cleanup, sanitised delivery/storage outcomes. Исключения
SDK и входящие тексты не логируются и не возвращаются пользователю.

Файлы: `apps/telegram-bot/{package.json,package-lock.json,tgcloud.jsonc,README.md}`;
`tgcloud/{schema.js,handlers/*.js,lib/*.js}`; `tests/*.mjs`, `tools/check.mjs`;
`.gitignore` для `.tgcloud/`, ADR/runbook/этот report, ROADMAP/dependencies.
Предыдущие Android UI edits сохраняются отдельной частью working tree.

## Что действительно запускалось

Node **24.15.0**, npm **11.12.1**.

Из `apps/telegram-bot`:

```powershell
npm install --ignore-scripts --no-fund
npm run check
npm test
npm audit --json
```

- Install: 22 dev dependency packages; lifecycle scripts выключены.
- Runtime check: **7 modules**, синтаксис/import boundaries/config; найденный
  набор совпал с настоящим scanner закреплённого `@tgcloud/cli@0.2.0`.
  Node-only API, test doubles и static hosting config в runtime не обнаружены.
- **44 tests passed, 0 failed/skipped** (финальный запуск ~174ms).
- SQL действительно выполнялся на локальном SQLite через Node `node:sqlite`.
  Telegram `sdk`/Bot API и schema DSL были **TEST ONLY substitutes**.
- Проверены десять параллельных повторов одного update: один send attempt;
  user/global caps; expired records cleanup; unknown delivery без повторной
  отправки; ошибки DB fail closed; rejected payloads и конфигурация URL.
- Npm audit: **0 reported vulnerabilities**. Это результат advisory database,
  не полный supply-chain audit или разрешение public release.
- Registry/package integrity для CLI 0.2.0 проверено при подготовке; лицензия MIT,
  версия pinned, transitive versions/integrities — lockfile.

Machine-readable evidence: `docs/evidence/telegram-bot-local.json`.

## Незакрытые условия

**Это не завершённый запуск бота и не выполненная авторизация Android через
Telegram Serverless.** Deep links входа fail closed: код не выдаётся. Для того же
бота нельзя менять webhook до защищённого relay к существующему FastAPI auth.
Механизм runtime secret injection в аккаунте владельца не проверен; открытая SDK
reference такого интерфейса не описывает. Browser inventory не удалось получить
из-за ошибки инициализации CUA (`failed to write kernel assets`, os error 3).
Следовательно отсутствие Secrets в early-access UI не утверждается.

Ни `login`, `push`, `migrate`, `run`, webhook sync, ни сообщения реальным
пользователям не выполнялись. Hosted V8, Telegram SQLite migrations, platform
privacy/log retention и доставка сообщений не проверены. Публичных URLs нет:
Mini App/download/support показывают честный unavailable state.

Backend, OpenAPI, PostgreSQL, OTP/security model, Android/VPN и Mini App source
этой задачей не менялись; их прежние test suites заново не запускались, поскольку
новый бот с ними не интегрирован. Никаких VPS, аккаунтов, DNS и production операций.

Следующий шаг: проверить Serverless panel/secret API и HTTPS auth ingress, затем
реализовать/принять trusted relay и выполнить согласованный staging-chat drill.
Ручные настройки, retention и rollback — `docs/runbooks/telegram-serverless-bot.md`.

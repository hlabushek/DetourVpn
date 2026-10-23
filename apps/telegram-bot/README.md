# DetourVpn Telegram bot — native Telegram Serverless

2026-10-22: menu deployment согласован владельцем и выполнен для **@DetourVpnBot**,
cloud revision 2, две таблицы/indexes и 6 private-chat commands подтверждены.
Hosted import/SQLite probe прошёл; user-chat `/start` acceptance ещё ожидается.
Receipt: `../../docs/evidence/telegram-bot-deployment.json`.

JS-проект меню для платформы из [документации Telegram](https://core.telegram.org/bots/serverless).
Реализованы `/start`, `/cabinet`, `/android`, `/connect`, `/help`, `/privacy`,
inline navigation и возврат в главное меню. Нажатия редактируют существующее
сообщение. Ссылки задаются в `tgcloud/lib/config.js`; пока они пусты, бот явно
объясняет недоступность, без неработающих URL.

**Вход в Android через этот serverless handler пока недоступен.** Не переключать
на него действующий auth-бот: новый handler не выдаёт OTP и не пересылает
неподтверждённые Telegram ID в API. Проверяемый secret manager/relay — открытый
gate ADR-032. Код FastAPI авторизации остаётся в прежнем состоянии.

## Локальная проверка

Node 24.15.x; зависимости закреплены в `package-lock.json`.

```powershell
npm ci --ignore-scripts
npm run check
npm test
```

Команды выполняются из `apps/telegram-bot`. `check` проверяет native module layout,
импорты, синтаксис, отсутствие Node API/test adapters в runtime и operator links.
`test` запускает SQL на настоящем локальном SQLite; SDK/Bot API delivery —
**TEST ONLY**, без сетевых запросов и без token. Production runtime — только
`tgcloud/`; `tests/`, `tools/`, npm packages не исполняются платформой.

## Дальнейшая настройка

Публичные HTTPS ссылки: `miniAppUrl`, `androidUrl`, `supportUrl`, `privacyUrl`.
`botUsername` нужен для команд с `@BotName`. Стандартные команды без mention
работают с пустым username. Не вставлять credentials в эти поля.

CLI — `@tgcloud/cli@0.2.0`, dev-only, MIT. SDK предоставляется Telegram, сторонних
runtime библиотек нет. Lockfile содержит integrity dependency tarballs. Early
access не является доказательством стабильности или production suitability.

Deployment, миграции, команды BotFather, privacy/retention, rollback и необходимые
ручные шаги: [runbook](../../docs/runbooks/telegram-serverless-bot.md).
Фактические проверки: [report](../../docs/verification-telegram-bot.md).
Архитектурная граница: [ADR-032](../../docs/adr/032-telegram-serverless-bot.md).

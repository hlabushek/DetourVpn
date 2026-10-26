# DetourVpn Telegram bot — native Telegram Serverless

2026-10-22: menu deployment согласован владельцем и выполнен для **@DetourVpnBot**,
cloud revision 2, две таблицы/indexes и 6 private-chat commands подтверждены.
Hosted import/SQLite probe прошёл; user-chat `/start` acceptance ещё ожидается.
Receipt: `../../docs/evidence/telegram-bot-deployment.json`.

2026-10-26: beta Serverless отдельно включён для **@uysdgyfudf_bot**; revision 1
содержит 8 модулей, применены две operational SQLite tables. `miniAppUrl`
указывает на отдельный beta API host. Основной **@DetourVpnBot** остаётся на
revision 2 без изменений. Hosted `runtimeProbe` подтвердил отсутствие env/secret
globals. Реальное сообщение `/start`, Mini App initData и Android OTP ещё не
проверены; приглашения друзьям не отправлялись.

JS-проект меню для платформы из [документации Telegram](https://core.telegram.org/bots/serverless).
Реализованы `/start`, `/cabinet`, `/android`, `/connect`, `/help`, `/privacy`,
inline navigation и возврат в главное меню. Нажатия редактируют существующее
сообщение. Ссылки задаются в `tgcloud/lib/config.js`; beta Mini App URL задан,
прочие ссылки остаются пустыми и явно обозначаются как недоступные.

Serverless handler отвечает за меню и не принимает `/start` Updates для Android
OTP. Локальный вход реализован отдельным Mini App → FastAPI → Android потоком:
FastAPI сам проверяет Telegram `initData` и отправляет OTP через Bot API.
`runtimeProbe` в разделе Endpoints — только диагностика runtime globals; Mini App
его для входа не вызывает. Beta Serverless webhook синхронизирован и обрабатывает
beta bot updates; webhook основного бота не менялся. Реальный Telegram/Android
login ещё не принят.
Текущий интерфейс BotFather не показывает отдельный Secrets раздел; CLI Access
токен предназначен для CLI операций и не является runtime secret. См. I-026,
ADR-043/046 и `docs/verification-telegram-bot.md`.

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

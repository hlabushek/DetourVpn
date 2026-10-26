# Telegram Serverless bot — фактическая проверка

## Текущий статус I-026 — 2026-10-26

На отдельном beta bot владелец прошёл в Telegram Android Mini App → OTP →
авторизованный Android cabinet happy path. Это подтверждается
`docs/evidence/i-026-live-miniapp-otp-20261026.json`. Не проверены hosted
повторное использование того же `initData`/OTP, независимое подтверждение
Bot API delivery на сервере и device pairing. Более ранние нижеописанные
снимки, где bot URL пуст или HTTPS отвечал 404, относятся к состоянию до
последующей активации и не описывают этот подтверждённый happy path.

Privacy-minimal сохранение результата `sendMessage` развернуто на отдельном beta
API host; schema revision `0017_login_delivery_receipt`, API readiness/health=200.
Статусы: `pending`, `api_accepted` или `unknown` плюс время. `api_accepted`
означает только успешный ответ Telegram Bot API, а не факт прочтения. После
выкладки реальный OTP не запрашивался, поэтому строка с подтверждённым live
результатом ещё не собрана. Владелец сообщил, что при прежнем входе включал Sota
Connect, чтобы Telegram работал; это не было тестом прямого доступа или
DetourVpn. Evidence: `docs/evidence/i-026-beta-otp-receipt-deploy-20261026.json`.

## Обновление 2026-10-26: owner Android sign-in

После beta Mini App activation владелец сообщил, что вошёл в Android-клиент
через beta bot flow. После входа приложение показало отдельную ошибку загрузки
AWG engine; это согласуется с тем, что auth завершился до native-engine init.
Ошибка устранена включением проверенных `libwg-go.so` в debug APK. Это
owner-reported live sign-in, не независимый Bot API delivery receipt. Telegram
ID, OTP, raw `initData`, session и личные сообщения в evidence не записывались.
Повторный OTP/replay, user-device pairing и VPN tunnel не подтверждены.

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

## I-026: Serverless runtime probe — 2026-10-25

В BotFather overview отдельный раздел Secrets не отображался; раздел Endpoints
был пуст. По просьбе владельца создан и сохранён `runtimeProbe`. Он возвращает
только булевы признаки наличия стандартных env globals, secret/env-полей context
и SDK exports; ни значения секретов, ни имена переменных не читает. Endpoint
синхронизирован из cloud snapshot revision 3 в локальный checkout. `tgcloud run`
и BotFather Console оба завершились успешно; все шесть признаков — `false`.
Локальный `npm run check`: **passed, 8 modules**.

Это подтверждает отсутствие очевидного runtime secrets API в проверенном
исполнении, но не исключает непросмотренную/непривязанную настройку аккаунта.
Disposable secret не настраивался. Endpoint не реализует Telegram Update relay,
не меняет webhook, не вызывает Bot API и не пишет в DB; реальный OTP login
остаётся неподключённым. Probe receipt: `docs/evidence/i-026-serverless-runtime-probe.json`.

Официальное описание `Telegram.WebApp.Serverless.call` уточняет модель:
вызовы endpoint из Mini App передают `initData`, проверенный Telegram.
Это аутентифицирует Mini App-вызов, но документация не описывает общий
механизм runtime secrets или доставку обычных Bot API Updates в endpoint.
Следовательно, это может обойтись без Serverless → API secret для входа в
кабинет через уже существующую проверку `initData`, но не закрывает `/start` →
OTP → Android login. Источник: <https://core.telegram.org/bots/webapps#serverless>.

CLI `tgcloud` подтверждает назначение `Serverless → CLI Access → Access token`:
это credential для операций CLI (`login`, `push`, `pull`, `run` и др.),
хранящийся в локальном `.tgcloud/credentials`; он не передаётся endpoint как
runtime secret. Проверено по закреплённому исходнику `@tgcloud/cli@0.2.0`,
`tgcloud --help` и текущему BotFather UI, где видны только CLI Access, Handlers,
Endpoints, Library, Database и Mini App Files; отдельного Secrets раздела нет.
При просмотре CLI Access значение токена попало в accessibility output. Оно не
копировалось и не использовалось; владелец отложил ротацию. Credential не
использовать и не открывать повторно; вопрос ротации пересмотреть до будущих
cloud CLI операций. Значение не включено в репозиторий или evidence.

## I-026: локальная Mini App → Android OTP интеграция — 2026-10-25

Чтобы не вводить неподтверждённый Serverless secret relay, `/start <request_id>`
открывает сообщение с кнопкой Mini App, если оператор настроил HTTPS URL. Кнопка
передаёт только случайный 32-hex attempt ID во fragment URL. Mini App читает
fragment локально и отправляет к FastAPI raw Telegram `initData` и attempt ID;
backend проверяет подпись/свежесть, однократность `initData`, срок/состояние
attempt, subject binding и OTP limits, после чего использует существующий
`TelegramSender`. Ответ API содержит только `processed` или `delivery_unknown`.
Код, verifier и session token не передаются в URL/API response; Mini App не
создаёт кабинетную или Android-сессию в этом flow. Android продолжает свой
существующий `/auth/verify` и pairing.

ADR-043 фиксирует контракт. Локальные checks: API targeted suite **37 passed,
1 skipped**, включая disposable real PostgreSQL, synthetic signed `initData`,
TEST ONLY sender, invalid signature, replay/concurrency, identity rebinding,
delivery unknown/no retry и OpenAPI contract; Mini App **7 passed**, strict
TypeScript + Vite build passed; bot **45 passed**, runtime check **8 modules**.
Machine evidence: `docs/evidence/i-026-mini-app-otp-local.json`.

Это не live acceptance. Не проверены Telegram-issued `initData`, кнопка в
реальном чате, отправка настоящего кода и Android verify. Нужны постоянная
изолированная HTTPS beta-среда/API/DB/node, сборка/раздача Mini App и настройка
непубличного menu URL. Текущий menu URL пуст; A временный, B/production не
использован. Webhook, BotFather конфигурация и облачная публикация не менялись.

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

## Исторический pre-activation срез — 2026-10-26

**Это не выполненная авторизация Android через Telegram Serverless и не live OTP
acceptance.** Исторический pre-deployment отчёт выше описывал состояние на
2026-10-22; актуальные локальная Mini App → FastAPI OTP реализация и runtime
probe зафиксированы в разделах I-026 выше. Текущая BotFather проверка не нашла
Secrets в списке Serverless разделов и не обнаружила runtime env/secret API в
проверенном исполнении; она не исключает скрытую настройку или недокументированную
функцию. Telegram-issued `initData`, live доставку OTP и Android verify пока не
принимали.

Ни `login`, `push`, `migrate`, `run`, webhook sync, ни сообщения реальным
пользователям не выполнялись. Hosted V8, Telegram SQLite migrations, platform
privacy/log retention и доставка сообщений не проверены. На 2026-10-26 API host
и отдельная VPN-нода развернуты частично; API остаётся loopback-only.

По ADR-055 локальный menu config допускает canonical global IPv4 HTTPS origin
только для Mini App URL. Beta API host получил доверенный Let's Encrypt IP
certificate. Активный Caddy намеренно отвечает 404 для всех HTTPS путей; API и
Mini App не опубликованы. Рабочий proxy template read-only прошёл `caddy validate`
на точном host Caddy 2.11.7 с текущими beta IPs, но не установлен. Локальные
Serverless tests — **58 passed**, runtime boundary check passed; Mini App — **8
passed** и build passed; API Mini App/PostgreSQL — **13 passed** на удалённом после
тестов временном кластере. IP origin не прописан в Serverless config, BotFather
не менялся. Live Telegram `initData`, OTP delivery и Android verify неприняты.
Отдельные записи: `docs/evidence/i-026-mini-app-otp-local.json` и
`docs/evidence/i-030-beta-api-tls-20261026.json`.

### Локальная перепроверка OTP-пути 2026-10-26

После выбора beta username локальная конфигурация `tgcloud/lib/config.js`
указывает на `@uysdgyfudf_bot`; опубликованный `@DetourVpnBot` не изменялся.
Serverless bot: **58 passed**, runtime check — **8 modules passed**. Mini App:
**8 passed**, TypeScript/Vite build — **passed**. API Mini App tests:
**13 passed** на отдельном временном PostgreSQL; runner остановил сервер и удалил
сгенерированный кластер. Все Telegram `initData` synthetic TEST ONLY; внешних
запросов не было. Это усиливает локальную проверку контракта, но не закрывает
hosted gate: beta API/Mini App URL пока закрыт HTTPS 404, BotFather не настроен,
живой Telegram-issued `initData` → OTP delivery → Android verify не выполнялся.

Этим локальным follow-up затронуты только URL validation/config docs; API,
OpenAPI, PostgreSQL и Android/VPN source не менялись. Никаких BotFather, DNS,
production операций или публикации этим изменением не выполнялось.

Следующий блокер — безопасно открыть beta API/Mini App: на хосте пока выключены
auth, Mini App и invite-only, MFA-admin не настроен; поэтому прокси остаётся
закрытым, даже несмотря на исправный TLS. Перед публикацией надо безопасно
настроить beta-only auth и Bot API credential, включить invite enforcement,
подготовить admin MFA и проверить, что публичны только необходимые маршруты.
Затем понадобится настройка Mini App для `@uysdgyfudf_bot` и live Telegram
webview → real `initData` → OTP → Android verify. Тестовый Bot API token не
вставлять в чат; опубликованный `@DetourVpnBot` не переключать. Сертификатный
renewal timer уже проверен, но независимое expiry alerting остаётся открытым
I-027 gate. Ручные настройки, retention и rollback —
`docs/runbooks/telegram-serverless-bot.md` и `docs/runbooks/beta-environment.md`.

### Beta Serverless deployment — 2026-10-26

Это более позднее beta-only состояние; вышеописанные закрытый API и отсутствие
Mini App URL отражают проверки до активации. Для отдельного
`@uysdgyfudf_bot` CLI status/diff подтвердили пустой remote revision 0 перед
публикацией; основной `@DetourVpnBot` не менялся. Опубликованы 8 modules,
`migrate --safe` применил только `bot_menu_limits` и `bot_menu_updates`, без
skipped changes. Итог: revision 1, webhook in sync, clean status. Hosted
`runtimeProbe` вернул false для всех проверок process/env globals и secret
context/SDK fields. Сообщения пользователям не отправлялись.

Это подтверждает развёртывание обработчиков и синхронизацию webhook, но не
реальное получение `/start <request_id>`, Telegram Mini App `initData`, OTP или
Android verification. Invite-only на API требует заранее выдать приглашение
точному Telegram ID. Receipt:
`docs/evidence/i-030-beta-serverless-deployment-20261026.json`.

### Owner-only Mini App → Android OTP happy path — 2026-10-26

Позднее live-проверка на Android подтвердила owner-only happy path: beta bot
login message открыл Telegram chat, владелец запустил Mini App и ввёл код только
в Android app; Telegram webview показывал инструкции безопасного ввода. После
этого Android app показал авторизованный beta кабинет, VPN выключен, остаток
337868 байт. OTP, raw `initData`, Telegram ID и URL-параметры не сохранялись.
Это реальный клиентский happy-path сигнал, но не независимый server-side
delivery receipt: correlation ID и Bot API response не записывались. Hosted
replay/duplicate rejection не выполнялись; I-026 остаётся открытой для этих
negative checks, а I-027/I-028 остаются самостоятельными воротами до приглашения
друзей. Evidence: `docs/evidence/i-026-live-miniapp-otp-20261026.json`.

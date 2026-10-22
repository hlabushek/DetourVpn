# ADR-001: вход по одноразовому коду из Telegram-бота

Status: **Proposed** (технический контракт)  
Updated: 2026-10-18 / I-005–I-006  
Product decision: **подтверждено владельцем 2026-10-18** — авторизация через код,
сгенерированный в Telegram-боте. Повторное согласование основного способа не нужно.
Предыдущий план email + Telegram OIDC заменён этим решением.

## Пользовательский сценарий

1. В приложении пользователь нажимает «Получить код в Telegram». Backend создаёт
   попытку входа; приложение открывает нашего бота по ссылке.
2. В личном чате пользователь запрашивает код. Сервис бота генерирует одноразовый
   код для этой попытки, связывает его с Telegram user ID отправителя и показывает
   код в личном чате. Если generator расположен в backend, он доступен только
   доверенному bot adapter; для пользователя код выдаёт именно бот.
3. Пользователь вводит код в приложении. Backend проверяет попытку, клиентский
   секрет, код, TTL и лимиты; атомарно погашает код и создаёт сессию аккаунта,
   к которому привязан подтверждённый Telegram user ID.
4. Повторный вход с тем же Telegram ID использует существующий аккаунт и баланс;
   повторное погашение не создаёт другую сессию и не начисляет стартовую квоту.

Начало в приложении — проектное допущение для безопасного pairing: короткий код
не должен быть общим ключом поиска по всем аккаунтам. При открытии бота без pairing
он предлагает начать вход в приложении. Возможный сценарий «сначала бот» требует
отдельного высокоэнтропийного claim-контракта; короткий standalone код не добавлять
неявно. Email, OIDC Login и Mini App initData не являются выбранным методом входа.

## Pairing и одноразовый код — предлагаемые параметры

- `/auth/start` создаёт random public request ID не менее 128 bit и отдельный
  random verifier не менее 256 bit. В БД только digest verifier. Приложение хранит
  verifier до завершения попытки; он не попадает в bot link, clipboard или логи.
- Bot deep link содержит только public request ID. Параметр start укладывается в
  лимит Telegram; сам по себе он не подтверждает identity и не авторизует.
- Код — 8 случайных десятичных цифр из штатного CSPRNG, TTL 5 минут от выпуска.
  Pairing истекает через 10 минут от создания; окончательный expiry не позже обоих TTL.
  Успех требует одновременно request ID, verifier и code, а не только 8 цифр.
- Один активный code на попытку и один Telegram subject на неё. Нельзя перезаписать
  subject чужим bot request. Новая issuance инвалидирует предыдущий code, не продлевает
  исходный pairing TTL и не обнуляет общий budget неудачных проверок.
- Максимум 5 ошибочных code checks на попытку, resend не чаще 60 секунд,
  5 issuances/15 минут и 20/сутки на Telegram subject; общие network/global limits
  защищают от массовых попыток. Неверный verifier не раскрывает owner/code;
  общий лимитер нельзя обходить подстановкой несуществующих request IDs.
- Только HMAC-SHA-256(code + request ID + generation) с отдельным серверным pepper
  и versioned key, canonical encoding; сравнение штатным constant-time API.
  Короткий code нельзя хранить простым hash. Никаких своих crypto primitives.
- Код не сохраняется в plaintext, cache/outbox/log/trace. При неизвестном результате
  Telegram send не генерировать новый код на каждый retry Update; новая явная
  issuance после cooldown заменяет старый. Durable delivery, если понадобится,
  требует отдельного решения о шифровании/TTL секретного outbox.
- Verify, increment attempts, consume и создание user/session — транзакционно.
  Race из двух verify допускает ровно одно успешное погашение. Потерянный ответ
  не позволяет повторно получить bearer tokens по использованному коду:
  в первой версии начать новую попытку, не возвращать новую сессию.

Численные лимиты и pairing-контракт реализованы и проверены локально в I-005;
подробности, retention и ограничения — ADR-012. Устройства/session binding — ADR-013.
Техническое ADR остаётся Proposed для review; live Telegram/production не проверены.
Выбор входа через бот уже принят пользователем.

## Доверие к боту и identity

Identity — `(provider=telegram_bot, subject=Telegram user.id)`, уникальность в БД.
ID хранить как signed 64-bit integer либо каноническую строку, не username/float.
Не брать Telegram ID из публичного JSON приложения, текста команды, пересылки
или start payload. Использовать from.id из Update, полученного по доверенному каналу.
Код выдаётся только в private chat реального пользователя, не в группе,
inline/guest channel или другому боту. Bot token остаётся на сервере.

Для webhook: HTTPS, проверка X-Telegram-Bot-Api-Secret-Token через constant-time API,
ограничения body/rate и durable dedup по bot identity + update_id. Это проверка
shared secret, а не выдуманная Telegram-подпись JSON. Для long polling — запросы
на официальный Bot API с серверным token и корректное сохранение offset/dedup;
выбрать один transport при реализации. Если bot adapter отдельный, между ним и API
нужны service-to-service auth и узкий issue-code контракт.

API выдачи кода не доступен анонимному приложению и не принимает произвольный
Telegram ID. Bot API URL содержит token: его нужно редактировать в HTTP logs/errors.
Не собирать profile photo, phone number, контакты или переписку ради login.
Telegram subject — персональные данные; local retention описан в ADR-012.
Account deletion/recovery и production privacy policy остаются gates до реальных аккаунтов.

## Сессии, восстановление и ограничения

После code login предлагаются разные random opaque access/refresh tokens
не менее 256 bit, server-side SHA-256 digest, access TTL 10 минут, refresh absolute
TTL 30 дней. Refresh rotation одноразовая, reuse отзывает family; logout-all,
device revoke и recovery инвалидируют sessions. Device ID не доказывает владение.
Начальная квота — одна ledger operation на user_id. Несколько Telegram аккаунтов
не равны одному человеку: antiabuse остаётся отдельной задачей.

При потере Telegram, блокировке бота или outage нового входа через этот метод нет.
Действующая сессия работает в рамках своего срока/отзыва; VPN подчиняется отдельной
quota/lease policy. Не добавлять скрытый email fallback или поддержку, меняющую
identity по имени/скриншоту. Независимый recovery method ещё не выбран;
смена связанного Telegram subject требует отдельного защищённого процесса.

Код — секрет, его можно выманить. Приложение ясно показывает, в какой аккаунт
выполнен вход; бот предупреждает вводить code только в нашем приложении. Pairing
снижает перенос кода между попытками, но не устраняет phishing и компрометацию
Telegram/устройства/бота. Обычный bot-code login не предоставляет admin MFA/RBAC.

## Acceptance tests I-005/I-006

| Сценарий | Ожидаемый результат |
|---|---|
| Повтор одного Telegram update | Одна issuance, без повторного начисления |
| Просроченный / заменённый / использованный код | Отказ, сессия не создаётся |
| Два concurrent verify | Одно погашение и одна сессия |
| Верный код для чужого request/verifier | Отказ, нет переноса identity |
| Подделка Telegram ID / неверный webhook secret | Отказ |
| Group, forwarded identity, bot sender | Код не выдаётся по недоверенному subject |
| Resend / попытки / перебор request IDs | Лимиты не обходятся |
| Повтор login того же Telegram ID | Тот же user_id, квота не дублируется |
| Logout-all / refresh reuse / чужой device | Revocation и ownership соблюдаются |
| Delivery timeout / Telegram outage | Честная ошибка, нет фиктивного входа |
| Diagnostics/logging/DB dump | Нет raw code/verifier/token или Bot API token URL |

Новых auth-библиотек не добавлено: CSPRNG/HMAC/digests и Telegram HTTPS sender
используют Python stdlib; зависимости backend уже pinned. Authlib не нужен.
В I-002 auth API/миграций ещё не было. Локальный функциональный результат I-005/006
и фактические проверки описаны в docs/verification-identity-stage.md.

Первичные источники, проверены 2026-10-18:
[Bot API Update/User](https://core.telegram.org/bots/api#update),
[Webhook secret](https://core.telegram.org/bots/api#setwebhook),
[Bot deep links](https://core.telegram.org/bots/features#deep-linking).

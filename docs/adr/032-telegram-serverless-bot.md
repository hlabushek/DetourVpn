# ADR-032 — Telegram Serverless bot UI and authentication boundary

Status: **Menu deployed; local OTP integration implemented through Mini App/FastAPI; live Android login acceptance remains open**.
Дата: 2026-10-22. Назначенная владельцем задача: Telegram-бот после Android UI.

## Контекст

Владелец получил ранний доступ к встроенной Telegram Serverless и указал
https://core.telegram.org/bots/serverless. Проверены эта документация (включая
machine-readable .md) и MIT-пакет `@tgcloud/cli@0.2.0` из указанного Telegram npm
namespace. Платформа исполняет JS modules через `sdk`: Bot API, SQLite DB, HTTP.
Это не Node.js, FastAPI, PostgreSQL или VPN engine hosting.

Serverless управляет webhook бота; исходный вход I-005/I-013 ожидает trusted
Updates по `/api/v1/telegram/webhook`. Переключение одного и того же бота без
защищённого relay нарушило бы доставку кодов в Android.

## Решение и допущения

1. Отдельный `apps/telegram-bot` с двумя native handlers: message/callback_query.
   Меню, кабинет, Android, инструкция, помощь, описание обработки данных.
   Код runtime использует только `sdk`/`sdk/db` и relative `.js` imports.
2. Экономика, VPN, сессии, идентичность и OTP остаются в FastAPI/PostgreSQL.
   Бот не создаёт второй ledger или аккаунт по пользовательскому Telegram ID.
3. Ссылок владельца пока нет. В operator config — пустые значения, в UI — честные
   сообщения о недоступности. Балансы, rewards и статусы VPN в чате не выдумываются.
   Ссылка Mini App подключается как native web_app button только после настройки.
4. В открытой SDK reference нет документированного runtime secret manager/env API.
   Проверенный 2026-10-25 BotFather UI также не показывает отдельный Secrets/env
   раздел, а `runtimeProbe` не нашёл известных env globals/context fields/SDK
   exports. Это не доказывает отсутствие скрытой или недокументированной функции.
   Не придумывать `process.env`, SDK secrets API или хранение shared secrets в
   lib/DB. CLI Access token используется CLI и не является runtime secret.
5. Для Android OTP не требуется Serverless → API secret relay: ADR-043 выбрал
   Mini App → FastAPI flow, в котором FastAPI проверяет Telegram `initData` и
   отправляет код существующим `TelegramSender`. Serverless меню не должно
   самостоятельно выдавать код. Нельзя заменять webhook до hosted OTP acceptance
   и отдельного разрешения; BotFather push/webhook sync остаются внешними live
   действиями.
6. SQLite хранит только update ID/expiry и rate buckets. Каждый claim — уникальный
   INSERT; дубли не отправляются повторно. Claim коммитится до Bot API send/edit:
   timeout/crash может потерять ответ, поэтому at-most-once attempt, не exactly-once
   delivery. Новый `/start` — явная новая попытка пользователя.
7. Ограничения: 20 разрешённых menu operations/user/minute и 120/global/minute,
   fixed windows; на границе возможен burst. Это не DDoS-защита. Сначала claim,
   затем лимиты, поэтому rate-limited update тоже считается обработанным.

## Приватность и retention до любого live использования

- Исходные сообщения, names/usernames, файлы, OTP, ключи, URL посещённых сайтов,
  IP, balance/history не записываются кодом бота и не выводятся в console.
- `bot_menu_updates`: update ID и expiry с TTL 24 часа.
- `bot_menu_limits`: raw Telegram user ID только внутри minute bucket; срок
  действия до двух минут. Global bucket не содержит user ID.
- Cleanup удаляет expired rows при следующем допустимом обращении. Без новых
  обращений физические записи могут оставаться дольше TTL; это не background
  scheduler. Перед удалением бота оператор должен отдельно очистить его DB.
- Telegram видит чат и исполняет код; это отдельная trust boundary. Политики
  инфраструктурных логов и резервных копий Telegram этим кодом не контролируются.
  До реальных пользователей нужны отдельная privacy policy и review retention.

## Проверки и миграции

Два новых SQLite table + expiry indexes. `tgcloud push` публикует schema definition,
а `tgcloud migrate` отдельно применяет изменения после review. PostgreSQL не
меняется, Alembic migration здесь не требуется. Rollback: сначала восстановить
прежние handler modules; очистить/вывести таблицы из эксплуатации только отдельным
reviewed шагом. Простое удаление schema declaration не является DROP в tgcloud.

Тесты используют реальный local SQLite и TEST ONLY SDK/Bot API adapter; они не
подтверждают поведение Telegram V8/managed migrations или доставку в реальном чате.
См. `docs/verification-telegram-bot.md`, `docs/runbooks/telegram-serverless-bot.md`.

Обновление 2026-10-25: локальная Mini App → FastAPI → Android OTP интеграция
описана в ADR-043 и I-026. Её локальные тесты не являются hosted acceptance.

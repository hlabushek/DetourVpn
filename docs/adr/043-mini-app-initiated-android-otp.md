# ADR-043 — Mini App-инициированный Android OTP

Статус: принято для локальной реализации I-026, 2026-10-25.

## Контекст

FastAPI уже выпускает OTP по доверенному Bot API Update и отправляет его через
`TelegramSender`. Telegram Serverless управляет webhook бота, но проверенный
runtime relay-secret API в аккаунте не обнаружен. Его CLI Access token — только
credential операций CLI. Серверные Mini App endpoints получают проверенный
Telegram `initData`, но не являются обработчиками Bot API Updates.

## Решение

Сохранить Android `/auth/start` → Telegram `/start <request_id>` → Android
`/auth/verify`. После `/start` бот показывает Web App кнопку только если
оператор настроил HTTPS Mini App URL. Кнопка передаёт случайный UUID попытки во
fragment URL; fragment не отправляется HTTP серверу. Mini App отправляет
FastAPI сырой `initData` и UUID. FastAPI проверяет Telegram HMAC, свежесть,
однократность initData, существование/состояние попытки, subject binding и
текущие OTP лимиты, затем использует уже настроенный `TelegramSender`. Код и
Android verifier не помещаются в URL или ответ API. Android сохраняет
существующий `/auth/verify` и сам получает конечную сессию.

Для запуска используется Telegram inline `web_app` button в ответе на
`/start <request_id>`. BotFather global Menu Button/Main App не являются
предварительным условием этого способа; inline launch и global menu/main app
описаны Telegram как разные варианты. Фактический Serverless `miniAppUrl` пуст,
пока не создан постоянный beta HTTPS origin. Same-Origin Restriction в
BotFather не заменяет проверку правильного origin или hosted acceptance.

FastAPI/PostgreSQL остаются единственным владельцем identity, OTP, replay,
rate limits и Android session. Не добавлять credentials в Serverless module,
SQLite или frontend. Mini App `initData` расходуется одной операцией: OTP
выдача либо кабинетный session exchange, без повторного использования.

## Последствия и ограничения

- Нужны same-origin HTTPS API + Mini App static hosting и настроенный bot
  Mini App URL для живого прогона; этот ADR ничего не публикует и не меняет
  webhook.
- Ошибка доставки Telegram считается неоднозначной; автоматической повторной
  отправки нет. Пользователь начинает новую Android попытку после лимита.
- Отказ в Mini App из-за недоступного challenge, beta invite или OTP лимита
  возвращается как `rejected` без Telegram DM. Webhook `/start` сохраняет
  прежние ответы для инициированного пользователем чата. Это не меняет допуск
  и не подтверждает live доставку.
- Fragment содержит только несекретный случайный attempt UUID; проверка UUID
  обязательна. `initData`, OTP и verifier нельзя писать в логи.
- Реальная доставка/Telegram webview/Android acceptance остаются отдельным
  hosted gate. Локальные synthetic HMAC tests не доказывают их.

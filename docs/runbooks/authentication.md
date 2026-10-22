# Локальная проверка входа и устройств

С I-007 клиент также показывает свою квоту в байтах. Необязательный размер
VPN_INITIAL_QUOTA_BYTES задаётся в локальном .env; default 0, один раз/user при
успешном code login. Изменение настройки не переоценивает уже выданную квоту.
Сверка и подробности — quota.md. Сам запрос кода не начисляет квоту.

Это backend-клиент для ручной проверки, не Android/Flutter приложение и не VPN.
Автоматические тесты используют реальный PostgreSQL и fake Telegram sender только
в тестах. Для них bot token, HTTPS и сообщения реальным людям не нужны:

```powershell
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
```

## Конфигурация настоящего тестового бота — вручную

Нужны собственный локальный PostgreSQL и уже имеющийся тестовый Telegram-бот.
В локальном редакторе скопируйте `.env.example` в игнорируемый `.env`, укажите
VPN_DATABASE_URL (см. database.md), VPN_TELEGRAM_BOT_TOKEN, его numeric bot ID и
username. Два **независимых** случайных секрета VPN_AUTH_PEPPER и
VPN_TELEGRAM_WEBHOOK_SECRET: каждый 43..128 URL-safe символов, например 32 random
bytes из локального password manager/CSPRNG. Никаких реальных значений в чат,
команды/history, репозиторий или скриншоты. Затем VPN_ENABLE_AUTH=true.

Не переиспользуйте production bot. Регистрация webhook требует явного решения
владельца и собственного одобренного HTTPS test ingress к локальному API:
Telegram не доставляет webhook непосредственно на 127.0.0.1. Этот проект не
создаёт tunnel/VPS/DNS и не регистрирует webhook автоматически. Только после
одобрения вручную вызвать официальный setWebhook с URL `/api/v1/telegram/webhook`,
secret_token из .env и allowed_updates=["message"]. Не вставлять token-bearing
URL в браузер/history/логи. Предыдущий webhook/long polling этого бота изменится;
проверить принадлежность тестового бота перед такой операцией. Приложение само
не вызывает setWebhook/deleteWebhook/getUpdates, только sendMessage в ответ на
доверенные Updates. До этих ручных действий **live доставка не настроена**.

Ограничение текущего local-only proxy режима: все входящие адреса от одного
reverse proxy считаются одним network bucket. Это безопасное ограничение, но
может дать общий 429. Не включать доверие произвольному X-Forwarded-For ради обхода.

## Запуск

Из корня репозитория:

```powershell
uv sync --frozen
uv run --frozen alembic upgrade head
uv run --frozen alembic check
uv run --frozen uvicorn vpn_api.main:create_app --factory --app-dir services/api/src --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

Миграции выполняются явно. В другом терминале:

```powershell
uv run --frozen python scripts/local_auth_client.py
```

Клиент показывает только публичную ссылку на бота. Откройте её, запросите код,
введите его в скрытом поле терминала. Он проверит `/me`, при желании зарегистрирует
**существующий публичный Android key**, покажет число своих устройств, обновит
сессию и выполнит logout либо предложенный отзыв своего устройства. Private key
не передавать. Клиент ключи не генерирует, VPN не создаёт. При отсутствии native
key регистрацию можно пропустить. Для Android UI/key generation нужна I-011/013.

Потерянный ответ verify/refresh требует нового входа; не повторять refresh
параллельно: reuse отзывает family. Каждая новая попытка через приложение даёт
новый pairing; в боте `/start` без pairing только предлагает начать в приложении.

## Retention и остановка

При запущенном local auth периодически выполнять cleanup; в отдельном терминале:

```powershell
uv run --frozen python scripts/auth_cleanup.py
```

Она не отправляет сообщений и не удаляет аккаунты/устройства; только истёкшие
auth records по ADR-012. Без расписания фактический retention увеличивается.
Для permanent local use настроить запуск, например раз в час, самостоятельно;
системное расписание этим изменением не создаётся. Остановка API — Ctrl+C.

Admin role не выдаётся через bot-code и не открывает административные операции
даже после ручной DB правки: enrollment/MFA не реализованы. Account deletion,
recovery, production secrets/TLS/backups и live Telegram проверка — отдельные gates.

Источники: [setWebhook](https://core.telegram.org/bots/api#setwebhook),
[deep links](https://core.telegram.org/bots/features#deep-linking).

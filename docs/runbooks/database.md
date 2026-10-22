# I-004: локальная БД и миграции

Команды выполнять из корня. Никакого автоматического upgrade при запуске HTTP API.
Нужен собственный PostgreSQL 17 на loopback: dev Compose или отдельно установленный
локальный сервер. Если порт — SSH-forward, не направляйте его на production.

## Запуск с dev Compose

1. Запустите Docker Engine вручную, скопируйте `.env.example` в `.env`.
2. В локальном редакторе задайте случайный VPN_DEV_POSTGRES_PASSWORD и соответствующий
   VPN_DATABASE_URL: driver postgresql+psycopg, user/database vpn_dev, адрес
   127.0.0.1:15432. Пароль в URL должен быть URL-encoded. Не вставляйте его в чат,
   командную строку/history или Git. Значения `.env` не подставляются друг в друга.
3. Проверьте config и поднимите только свой dev stack:

```bash
uv sync --frozen
docker compose --env-file .env -f infra/compose/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait
uv run --frozen alembic upgrade head
uv run --frozen alembic current
uv run --frozen alembic check
```

URL принимается только с loopback host, явными user/database и без query parameters.
Remote/Docker hostname postgres не принимается: API сейчас запускается на host,
а не в контейнере. TLS отключён только для этого локального режима.
Пустой VPN_DATABASE_URL разрешён для liveness API, но миграции без него откажут.
Не выводить `.env` или полный URL диагностическими командами.

После upgrade head: accounts, login_attempts, session_families/credentials,
bot_updates, code_issues, rate_buckets, devices, quota_operations/quota_ledger и
alembic_version; I-008 добавляет usage_streams/usage_epochs/usage_samples. Миграции не
создают реальных пользователей, credential defaults или fake Telegram credentials.
Авторизация включается отдельно по docs/runbooks/authentication.md.

## Откат

Сначала остановите своё приложение/worker, проверьте target и сделайте проверенную
резервную копию, если в local DB есть нужные данные. Для пустой test/dev схемы:

```bash
uv run --frozen alembic downgrade base
uv run --frozen alembic upgrade head
```

Downgrade с account/auth/device/quota rows откажет. Перед проверкой пустоты берутся table locks;
lock wait ограничен 5 секундами. Ошибка откатывает DDL и Alembic revision. Не удаляйте
данные ради прохождения этой команды: для изменения непустой схемы нужна новая
forward migration либо изолированный restore plan. Секретных force флагов нет.
`upgrade head --sql` генерирует PostgreSQL DDL без подключения; это не реальная миграция.

## Integration tests

Обычный `uv run --frozen pytest` без VPN_TEST_DATABASE_URL явно пропускает postgres
tests. Это не полная проверка I-004. Есть два пути полноценной проверки:

- Указать в локальном окружении VPN_TEST_DATABASE_URL своего disposable local server
  и выполнить pytest. User должен иметь CREATEDB; каждый тест создаёт случайную
  database с prefix vpn_i004_ и удаляет только её. Не использовать production URL
  или произвольный важный cluster, даже если он доступен через loopback.
- Запустить встроенный test runner с доверенными PostgreSQL binaries:

```bash
uv run --frozen python scripts/test_with_postgres.py --postgres-bin /path/to/postgresql/bin
```

Windows вариант (если runtime уже подготовлен в текущей рабочей папке):

```powershell
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
```

Runner не скачивает и не устанавливает PostgreSQL. Нужны initdb, pg_ctl и их штатные
runtime/share files. На Linux запускать от непривилегированного пользователя.
Runner создаёт отдельный cluster внутри .cache/postgres-tests, случайный пароль,
loopback port и тестовые БД. В конце stop + удаление только его cluster path.
При неудачном stop cluster остаётся для диагностики; его нельзя удалять до остановки.
Команда проверяет schema/ORM, auth/device API и concurrency. Telegram sender/HTTP
transport явно mocked только в tests; live Telegram API и настоящий VPN не проверяет.

В CI добавлен отдельный ephemeral PostgreSQL service без постоянных данных.
Trust authentication там предназначена только для isolated loopback runner;
dev Compose по-прежнему требует локальный пароль. Миграции не открывают admin API;
RBAC gate default-deny, отдельное admin MFA enrollment пока не реализовано.

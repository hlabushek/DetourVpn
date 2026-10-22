# Локальный запуск Telegram Mini App

Это I-017, не публичный выпуск. Настоящий токен настраивается только локально
в ignored `.env` согласно существующему auth runbook; не вводить его в чат,
shell arguments, frontend env или Git. Нужен тот же бот, что используется Android.

```powershell
npm ci --ignore-scripts --prefix apps/telegram-mini-app
npm test --prefix apps/telegram-mini-app
npm run build --prefix apps/telegram-mini-app
uv run --frozen python -m alembic upgrade head
```

В локальном `.env` явно включить `VPN_ENABLE_AUTH=true`, затем
`VPN_ENABLE_MINI_APP=true`, `VPN_MINI_APP_STATIC_DIR=<absolute path to apps/telegram-mini-app/dist>`.
Пустая static directory означает отсутствие раздачи, а не корень репозитория.
База остаётся только локальной согласно текущему DB policy. Не менять existing
initial quota/reward rules без отдельного экономического решения.

```powershell
uv run --frozen python -m uvicorn vpn_api.main:create_app --factory --app-dir services/api/src --host 127.0.0.1 --port 18767 --no-access-log
```

Кабинет: http://127.0.0.1:18767/mini-app/. Обычный браузер показывает приглашение
открыть Telegram, без фальшивого входа. Для разработки доступен
`npm run dev --prefix apps/telegram-mini-app` (Vite 5173, proxy на локальный 18767).
Vite dev server никогда не публиковать. Сборку `dist` раздавать с API на одном origin.
В production bundle отсутствуют test-only SDK/seed routes; они живут в pytest.

## Локальная проверка без Telegram

```powershell
$env:PYTEST_ADDOPTS = '-q --tb=short -k "mini_app or migrations or rewards"'
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
```

Опциональная ручная browser QA запускает disposable PostgreSQL/API на 18767,
тестовый SDK и синтетические награды, отмеченные TEST ONLY. Не запускать вместе
с другим сервером на этом порту:

```powershell
$env:VPN_MINI_APP_BROWSER_QA = '1'
$env:PYTEST_ADDOPTS = '-q --tb=short -k test_browser_cabinet'
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
# В течение 5 минут открыть http://127.0.0.1:18767/mini-app/
# Для раннего завершения из другого терминала в корне проекта:
New-Item -ItemType File .cache/mini-app-qa.stop -Force
Remove-Item Env:VPN_MINI_APP_BROWSER_QA
Remove-Item Env:PYTEST_ADDOPTS
```

Проверить daily → баланс, clicker → история, light/dark, 320/390 px, refresh,
logout. Harness сам закрывает сервер и runner удаляет свой кластер. Тестовый
SDK не доказывает работу Telegram initData generation/клиентского webview.

## Перед отдельным реальным запуском

Потребуются явно одобренный staging HTTPS origin и настройка Mini App/menu button
существующего тестового бота через BotFather. Этот этап ничего не публикует,
не регистрирует домены и не изменяет бота. Reverse proxy должен маршрутизировать
`/mini-app/` и `/api/v1/` на один origin; не логировать Authorization, request
bodies, initData, ответы входа, Telegram SDK launch fragments. Сохранить CSP.
Включить auth cleanup scheduler и согласовать retention; reward rules по умолчанию
выключены. Убедиться, что известен владелец HTTPS termination и доступны privacy/support.

В Telegram на физическом устройстве проверить новый вход, тот же аккаунт Android,
реальное получение/повтор награды, квоту, закрытие/повторное открытие, expiry,
ошибку сети и logout. Не хранить initData/токены в screenshots или receipts.
При потере входного ответа/reload открыть Mini App заново: payload одноразовый.
При offline logout RAM очищается; отзыв сервера не подтверждён и сессия истекает
не позднее чем через час после создания. Вне этого сценария logout отзывает
только текущую сессию кабинета, не Android VPN.

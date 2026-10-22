# Локальная разработка и матрица сред

## Матрица

| Среда | Реальное состояние | Доступ и данные |
|---|---|---|
| local | API запускается командой README; Compose опционален | Только loopback, синтетические данные, .env вне Git |
| test / CI | App factory без .env и внешних сервисов | TestClient, отдельный процесс smoke; no VPN mock |
| staging | Не создана; I-010 playbook подготовлен локально, но не запускался | Нужны отдельные scoped credentials, проверенные binaries и явное разрешение на выбранный стенд |
| production | Не создана, режим API не поддерживается | Будущие security/legal gates, раздельные секреты, никаких credentials в Codex |

API принимает только VPN_ENVIRONMENT=local/test. `.env` необязателен и читается
от корня запуска. Экспорт схемы и тесты используют `_env_file=None`.
При запуске вручную не включайте access log с IP/URL; используйте команду README.
Ошибки приложения/трейсы не являются утверждённой production log policy.

## Необязательные PostgreSQL и Redis

Нужны работающий Docker Engine и Docker Compose v2. Копируйте `.env.example` в `.env`
и локально задайте `VPN_DEV_POSTGRES_PASSWORD`: случайное значение не менее 24 символов,
например из менеджера паролей. Не вставляйте его в чат или команды, попадающие в history.
API не использует этот пароль. Пустое значение останавливает Compose до запуска.
Синтаксис Compose требует непустую строку, длину/случайность вы проверяете локально.

```bash
cp .env.example .env
# Отредактируйте .env в локальном редакторе.
docker compose --env-file .env -f infra/compose/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait
docker compose --env-file .env -f infra/compose/compose.yaml ps
```

Слушатели: PostgreSQL 127.0.0.1:15432, Redis 127.0.0.1:16379.
Не запускать на недоверенной/общей машине: Redis без пароля и доступен другим
контейнерам этой сети. Никаких production данных. DB schema/миграций в I-001 нет.
Образы зафиксированы digest, автоматически обновляться не будут.

Остановка с сохранением локальной БД:

```bash
docker compose --env-file .env -f infra/compose/compose.yaml down
```

Не добавлять `--volumes`, если нужно сохранить данные. Изменение POSTGRES_PASSWORD
после инициализации volume не меняет пароль уже существующей роли; для такой смены
нужна отдельная локальная процедура. Если Docker daemon не запущен, config может
проверить YAML, но health/запуск контейнеров остаётся непроверенным.

## Аудит зависимостей

Проверяются runtime и dev зависимости с точными версиями и hashes из lock:

```bash
uv export --frozen --no-emit-project --format requirements-txt --output-file .venv/audit-requirements.txt --quiet
uv run --frozen pip-audit --requirement .venv/audit-requirements.txt --disable-pip --require-hashes
```

Нужен доступ к публичной advisory базе. Запрос передаёт названия/версии Python
пакетов; production конфигурация туда не отправляется. Успешный audit означает
отсутствие известных findings на момент запуска, не полноценный security review.
SBOM и проверка licences полного native bundle обязательны перед публичным release.

## Частые ошибки

- Порт 8000 занят: выбрать другой локальный порт через `--port`, не останавливать чужой процесс.
- `uv` не найден: проверить установку 0.11.17 и PATH, открыть новый терминал.
- `ModuleNotFoundError`: запускать из корня с `--app-dir services/api/src`.
- Production environment не принимается: ожидаемое ограничение I-001, не отключать проверку.
- Изменён OpenAPI: перегенерировать скриптом, затем повторить `--check`.

# ADR-011: локальная PostgreSQL foundation

Status: **Proposed** (реализованные локальные допущения I-004 для review)  
Дата: 2026-10-18. Не меняет решения ADR-001/002/003 и не разрешает production deploy.

## Граница и варианты

Выбран PostgreSQL 17, SQLAlchemy 2.1 sync engine + psycopg 3 и Alembic.
Async driver пока не нужен для двух таблиц без пользовательского DB API; SQLite
не подходит как замена проверкам PostgreSQL constraints, locks и transactional DDL.
Миграции запускаются явно, не автоматически при старте FastAPI.

Первый revision `0001_accounts` создаёт `users` и `auth_identities` как foundation
для I-005. Sessions, OTP challenges, devices, RBAC, ledger и бот сюда не входят.
API health остаётся process liveness и не подключается к БД.

## Схема и принятые локальные допущения

- `users`: UUID, status pending/active/disabled, locale, created_at, verified_at.
  По умолчанию pending; verified_at не заполняется автоматически. Статус сам по
  себе ещё не реализует правила входа/доступа: они относятся к I-005.
- `auth_identities`: UUID, user_id FK, provider telegram_bot, provider_subject
  положительный BIGINT, created_at и nullable verified_at. BIGINT соответствует
  выбранному Telegram user ID; generic OIDC string provider сейчас не нужен.
- Uniqueness `(provider, provider_subject)` запрещает одну Telegram identity у
  двух аккаунтов; `(user_id, provider)` — две Telegram identities у одного аккаунта.
  Второй unique index обслуживает lookup по user_id; дублирующий индекс не добавлен.
- Индекс users.created_at предназначен для операционного списка по времени.
  Все даты timezone-aware; connection timezone UTC. UUID генерирует приложение,
  не требуется PostgreSQL extension или собственная криптография.
- FK использует RESTRICT, не CASCADE: случайное удаление user не стирает identity.
  Законная процедура удаления/retention и безопасная смена Telegram — отдельно,
  до появления реальных аккаунтов; I-004 не обещает работающий account deletion.

## Конфигурация и безопасность

URL — SecretStr; validation text скрывает input. Принимается только
postgresql+psycopg, явные username/database и loopback host, без query parameters.
Hostaddr/port/options задаются явно, чтобы PGHOSTADDR не менял адрес подключения.
TLS выключен только для этого local-only increment. Docker service hostname и
remote/staging/prod URL требуют отдельной конфигурации, не обхода validator.
Loopback порт может быть SSH-forward: проверка URL не удостоверяет происхождение
сервера; указывать только собственный dev/test PostgreSQL.

SQL echo выключен, SQLAlchemy hide_parameters включён. Это не очищает автоматически
PostgreSQL DETAIL/error text: не выдавать DB exceptions наружу и не логировать их
целиком. Pydantic errors() тоже не должен сериализоваться в публичный ответ.
Управление жизнью engine у вызывающего кода; transaction context commit/rollback явный.

Downgrade первой миграции берёт ACCESS EXCLUSIVE locks с bounded wait до проверки
пустоты. Непустая схема вызывает исключение; DDL/version rollback транзакционный.
Так concurrent commit не может попасть между проверкой и DROP. Автоматического
destructive force flag нет; для данных нужен backup/restore или новая forward migration.

## Evidence и дальнейшие gates

Проверки на настоящем PostgreSQL включают повторный upgrade, отсутствие ORM drift,
constraints/FK, transaction rollback, unique race и concurrent writer при downgrade.
Изолированный test runner создаёт свой cluster и случайные БД, использует случайный
пароль, останавливает сервер и удаляет только свои проверенные cache paths.
CI использует отдельный ephemeral loopback test service; trust разрешён только там,
не в dev Compose и не для пользовательских данных.

До staging нужны отдельные least-privilege migration/runtime roles, TLS/network
policy, backup+restore drill и retention/auth decisions. Это не часть локального I-004.
Результаты: [отчёт](../verification-i-004.md).

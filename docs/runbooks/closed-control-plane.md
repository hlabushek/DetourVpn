# I-021 — подготовка постоянного control plane

Это **подготовленный конфиг**, не выполненный deployment и не повседневный VPN.
Существующий bounded dataplane не подключать к этому окружению как ежедневный.
Следующие шаги на сервере допустимы только после отдельного разрешения владельца.

## Baseline и разрешение

Перед remote change выполнить read-only инвентаризацию: ОС, docker/compose,
systemd, занятые 80/443/8000/15433, firewall, диски, пользователи, существующие
containers/units/VPN, текущий reverse proxy, hostname/certificate и backups.
Не печатать env, ключи, полные inspect/journal или SQL exception details.
При конфликтах портов остановить подготовку, не отключать существующую службу.
В разрешении перечислить конкретный host и новые units/volume/firewall rules.
Здесь не выбран hostname и не получен TLS сертификат; ACME registration тоже
является внешним действием. Независимый провайдер не подтверждён.

## Одобренная установка на Linux (не выполнена)

1. Создать `detourvpn` system user без shell и отдельный release в `/opt/detourvpn`.
   Release/source/venv root-owned, runtime user не может менять исполняемый код.
   Подготовить доверенные Python 3.13, uv 0.11.17, Docker/Compose и Caddy отдельно;
   версии/лицензии проверить при выборе deployment baseline. Новые пакеты данная
   задача не устанавливает. `uv sync --frozen --no-dev` готовит runtime, `alembic`
   и штатный migration wrapper присутствуют в обычных dependencies.
2. `/etc/detourvpn` root-owned 0700. В secrets создать три **независимых** пароля:
   `pg-bootstrap`, `pg-migrator`, `pg-runtime`; только локальным password manager,
   не через аргументы/history/чат. Файлы должны быть читаемы PostgreSQL entrypoint
   внутри контейнера; mount ACL проверить локально. Compose secrets не шифруют
   исходные файлы. Владельцу хоста доступны все три; runtime API не выдавать.
3. Review `infra/closed/compose.yaml` и init script. Init выполняется только на
   новом пустом volume. Не удалять существующий volume при ошибке init: проверить
   фактические роли и восстановить отдельным reviewed forward SQL.
   `docker compose -f infra/closed/compose.yaml config --quiet`.
4. После разрешения установить database unit, `daemon-reload`,
   `enable --now detourvpn-database.service`. PostgreSQL port — только loopback
   15433, постоянный именованный volume, runtime роль без DDL/admin privileges.
5. В root-only `migration.env` хранить **только** VPN_DATABASE_URL роли
   vpn_migrator, DB detourvpn на 127.0.0.1:15433. EnvironmentFile формат systemd,
   не shell скрипт. Выполнить разовый systemd job с этим EnvironmentFile,
   WorkingDirectory=/opt/detourvpn и ExecStart:
   `/opt/detourvpn/.venv/bin/python scripts/closed_migrate.py`.
   Wrapper проверит роль, upgrade head, проверит schema drift и отзовёт UPDATE/
   DELETE на quota_ledger и все записи alembic_version у runtime.
   Не использовать `source` с недоверенным env и не передавать URL через CLI.
6. Создать root-only `api.env`: VPN_ENVIRONMENT=closed, VPN_ENABLE_AUTH=true,
   VPN_DATABASE_URL с ролью vpn_api (password URL-encoded), VPN_PUBLIC_ORIGIN с
   одобренным HTTPS origin. Остальные AUTH secrets/бот — как authentication.md;
   новые независимые pepper/webhook secrets, API docs выключены. Не включать
   Mini App static dir до сборки/проверки. Tunnel profile пока не устанавливать.
7. Установить API unit и только после успешной миграции запустить его. Запросы
   127.0.0.1:8000/api/v1/health и /ready должны давать 200. /ready не принимает
   migration/superuser роль, пустую/старую DB или standby. Отключить БД на новом
   разрешённом стенде: /ready=503, /health=200; включить и проверить recovery.
8. Review `Caddyfile` с существующим hostname. Он закрывает internal agent routes
   и readiness от public ingress, не пишет access log. Постоянный защищённый
   ingress агентов ещё не реализован — не открывать plaintext agent routes.
   Применять Caddy/DNS/ACME/firewall только после отдельного разрешения. Проверить
   certificate chain, HTTP→HTTPS и отсутствие публичного DB/8000/ready/internal.
9. Проверить restart API/БД/host, фактическое сохранение users/devices/ledger.
   Off-host encrypted backup и полный restore, независимый scheduler/monitoring,
   retention/journald policy — I-027. До этого не импортировать настоящих users.

## Обновление, откат и секреты

Сначала encrypted backup и изолированный restore drill по operations.md, затем
остановка API, owner migration, новый root-owned release, restart, /ready и
проверка auth/ledger. Нельзя выполнять destructive downgrade для совместимости.
При обратимо совместимой схеме вернуть release и restart. При несовместимой —
отдельный план restore с fencing всех credentials/commands до включения узлов;
не запускать старую DB рядом с действующим dataplane. Compose `down -v` запрещён.

Ротация DB: остановить API, изменить password роли локальным psql `\password`,
обновить root-only secret/env согласованно и запустить API. Init script не
ротирует существующий volume. Ротация pepper инвалидирует текущие OTP; webhook
secret/бот требуют согласованного external change. Не менять webhook меню-бота
без I-026 relay. Файлы/журнал не содержат URL/DNS/tunnel payload; минимальные
operational reports хранить до 7 суток, backup key отдельно от архивов.

## Доступ администратора

Реализован отдельно в I-023: [administration.md](administration.md).
Нужны явное включение admin и interactive Linux bootstrap с MFA; стандартного
пароля или dev bypass нет. Подписки выдаются административными операциями I-022,
платежей нет. Это описание кода, не подтверждение remote enrollment.

## Одобренный rollout B, 2026-10-23

Scope и rollback: [closed-rollout-proposal.md](closed-rollout-proposal.md).
`scripts/start_closed_rollout.ps1 -StageOnly` передаёт только явный allowlist
исходников, нормализует LF и проверяет SHA-256 manifest. Секреты/БД/кэши/сборки
не передаются. Директория назначения должна отсутствовать; найденную установку
скрипт не перезаписывает. `-InstallStaged` открывает отдельный интерактивный
терминал с SSH и sudo: пароль вводит владелец локально, не в чат.

`infra/closed/install-b.sh` проверяет точный IP/OS/архитектуру, свободные ресурсы,
порты, NTP и фактический firewall; сохраняет private baseline, блокирует автозапуск
дефолтной страницы Caddy при установке пакета. При ошибке останавливает только
новые API/Caddy, сохраняет БД и baseline; firewall additions остаются для
осознанного rollback по сохранённому состоянию. Резюмировать после частичного
запуска нужно по фактическому состоянию; слепой повтор запрещён preflight.

Если UFW отключён, installer не включает его. Продолжение допускается только
при отсутствии nft input hook и legacy IPv4 INPUT rules/drop policy; иначе
нужен review baseline. При включённом UFW добавляются только TCP 80/443 rules.

Первый этап устанавливает DB/TLS/renewal timer. Без `/etc/detourvpn/api.env`
API остаётся выключенным; HTTPS может отвечать 502 на API/admin до подключения
авторизации. Это не готовый сервис. Никаких фиктивных bot credentials.
Существующий Telegram Serverless CLI уже привязан, revision 2; повторный login
и redeploy меню не нужны. Выдача OTP ещё требует отдельной интеграции I-026.
`configure-auth.py` — optional interactive root utility для реального Bot API
token; не запускается автоматически и не заменяет Serverless relay.

После auth configuration: проверить Settings локально без печати env, старт API,
readiness/права роли/HTTPS, enrollment admin, reboot и сохранение данных.
Timer проверяет expiry <48h через failed unit; внешнее уведомление ещё I-027.

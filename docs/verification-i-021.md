# I-021 — проверка 2026-10-23

Реальный временный PostgreSQL 17; существующие server data не использовались.
Первый набор: 38 passed (closed config, URL validator, migrations, schema drift,
защищённый downgrade). Дополненный closed набор: 16 passed, включая создание
отдельных vpn_migrator/vpn_api, запуск migration wrapper настоящим subprocess,
runtime DML и чтение после dispose engine, отказ DDL/schema version/ledger delete.
Mypy 116 source files passed; targeted Ruff/format passed. OpenAPI экспортирован.
Compose config --quiet passed, Docker daemon отсутствует: запуск container,
systemd, Caddy/TLS, автозапуск после reboot **не проверены**.

Одна промежуточная ошибка теста role setup: PostgreSQL format() требовал явный
text cast bind parameters; исправлено, cleanup защищён при частичном setup.
Финальный targeted run: 16 passed / 14.10s, temporary cluster stopped/removed.

Read-only SSH baseline двух существующих staging hosts: vpn-deploy, Linux;
Docker active на A / inactive на B, project-vpn-i012-agent inactive на обоих.
Это ограниченная инвентаризация, не полный deployment preflight и не разрешение.
Никаких удалённых записей, unit/firewall/DNS/webhook изменений или сообщений.
Android не менялся, физические тесты не проводились.

## Подготовка разрешённого rollout (последующее состояние)

Владелец явно разрешил предложенный rollout backend/PostgreSQL/admin на B.
Подготовлен отдельный `infra/closed/install-b.sh` и локальный launcher.
Проверены Bash и PowerShell parser, Ruff/format interactive auth helper,
`uv run python -m pip_audit -r infra/closed/certbot-requirements.txt --disable-pip`:
No known vulnerabilities found. Certbot 5.8.0 и closure закреплены версиями/хешами.
Пакет исходников передан в private home vpn-deploy, удалённый SHA-256 check и
`bash -n` прошли. Это source staging, **не запуск постоянных служб**.
Привязка Serverless CLI проверена read-only: saved app, revision 2, webhook in sync,
working directory clean. Bot/API integration не проверена этим status.

Промежуточные ошибки launcher исправлены до привилегированного запуска:
OneDrive reparse placeholder ошибочно считался symlink; Windows backslashes
терялись при WSL path conversion. Теперь actual LinkType и slash-normalized path.
Sudo требует локального ввода владельцем; API не запускается без настоящей auth
configuration. Нет нового admin, Telegram сообщения/webhook не изменялись.

## Фактический инфраструктурный запуск B

После локального sudo-ввода установка завершила PostgreSQL/migration и TLS phase.
Docker 29.1.3, Compose 2.40.3, Caddy 2.6.2 (Ubuntu package versions в installer),
uv 0.11.17/Python 3.13.12 установлены. Database unit active, порт только loopback
15433. Caddy active, TCP 80/443. Certbot прошёл staging issuance, trusted issuance
и renewal dry-run с deploy hook (timer включён только после successful return).
Плановый renewal ещё не наблюдался. External expiry alert delivery не реализован.

Обычный TLS client: TLSv1.3, доверенная цепочка, IP SAN 85.209.132.205, срок до
2026-10-30T04:55:10Z. Public ready/internal =404. API/admin =502 в этой фазе:
auth env отсутствует, backend преднамеренно не запущен. Независимый socket probe
с существующего A: 8000/15433 и control 65001 -> ConnectionRefusedError.
Windows connect-only давал connect+EOF даже на control port; результат помечен
непригодным для вывода о доступности сервиса. Sanitized receipt:
`docs/evidence/closed-infrastructure-20261023.json`.

Выяснено: ufw.service active не означает firewall enabled. На B ENABLED=no;
installer сохраняет это состояние, отказывается при обнаружении nft input hook
или legacy INPUT policy вместо автоматического enable/reset. Первый запуск
остановился на старой preflight-проверке до установки; проверка исправлена.

Admin-only setup ADR-037: targeted real PostgreSQL regression **37 passed / 51.65s**,
включая explicit config negatives, MFA/admin access и disabled user auth. Ruff,
format, mypy трёх изменённых Python files и Bash/PowerShell parsers прошли.
Подготовлен отдельный interactive finish-admin-b.sh; source hash check на B
прошёл. Owner enrollment, live admin login, API/DB/host restart и сохранение
пользовательских записей ещё не приняты. Daily VPN и Serverless relay не готовы.

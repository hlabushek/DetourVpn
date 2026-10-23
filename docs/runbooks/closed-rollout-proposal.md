# Предложение постоянного control plane — только после разрешения владельца

2026-10-23. **Владелец явно одобрил этот scope; шаги ещё не выполнены.** Это rollout backend/admin, не разрешение
или приёмка daily dataplane, Telegram webhook либо public launch.
Код I-021–023 подготовлен; фактические проверки — verification-i-021/022/023.

## Baseline (read-only SSH, 2026-10-23)

Оба существующих staging hosts доступны под vpn-deploy с прежним локальным
ключом, не root credentials. A=45.10.166.106, B=85.209.132.205; Ubuntu 24.04.5.
На A Docker active, Caddy/nginx inactive, TCP 80/443/8000/15433 не слушаются;
около 5.5 GB свободного диска. На B Docker/uv не найдены, Caddy/nginx inactive,
слушаются только SSH/DNS stub; около 6.45 GB свободного диска, RAM 961 MiB,
MemAvailable около 614 MiB, swap отсутствует. Это snapshot, не гарантия.
Staging I-012 agents inactive. Существующие VPN нельзя выключать/перенастраивать.
Перед изменениями повторно проверить CPU/RAM, UDP listeners, firewall, список
действующих units/containers. Secret env/inspect/journal не печатать.

Кандидат: **B для API/PostgreSQL/admin**, A пока не менять. Same-provider риск
остаётся. Node slots/постоянный agent — отдельный последующий reviewed rollout.
На 1 GB RAM ёмкость пока не измерена. Подготовлены: один API process, 32 HTTP
requests одновременно, один 64 MiB Argon2 KDF, PostgreSQL max_connections=25,
shared_buffers=128MB/work_mem=2MB/max_wal_size=512MB. Если ресурсов недостаточно,
остановить только новые службы, без покупок или отключения существующего VPN.

## Конкретный scope предлагаемого разрешения

1. На B установить из проверенных официальных источников Docker/Compose,
   pinned uv 0.11.17/Python 3.13 и Caddy; locked runtime dependencies.
   Certbot отдельно, >=5.4 с IP/webroot support; конкретные версии/хеши/licenses
   записать до запуска. В Python API dependencies Certbot/Caddy не добавлять.
2. Создать system user detourvpn, root-owned release `/opt/detourvpn`, private
   `/etc/detourvpn`, `/var/lib/detourvpn/acme`, отдельный PostgreSQL volume.
   Независимые DB passwords/auth/admin keys генерировать локально, передавать
   только private files/secret channels. Для sudo владелец вводит пароль только
   локально либо сам запускает reviewed bootstrap. Root SSH credentials не нужны.
3. Установить только новые detourvpn-database/API units и Caddy. DB — loopback
   15433, API — loopback 8000. Выполнить closed_migrate.py ролью vpn_migrator,
   проверить schema drift и runtime permissions. Миграции не создают пользователей.
4. После firewall baseline export добавить только входящие TCP 80/443 rules;
   не flush ruleset, не менять UDP/VPN/SSH. Сначала Caddyfile.ip-bootstrap:
   challenge доступен, API не опубликован.
5. Создать ACME account и запросить IP certificate для **85.209.132.205**:
   сначала LE staging, затем trusted certificate после проверки. Это внешняя
   регистрация/согласие с условиями CA и выпуск публичного сертификата — approval
   должен включать это явно. Нет покупок/domain/DNS registration. IP/сертификат
   попадут в публичные certificate transparency logs.
6. Установить Caddyfile.ip и PEM root:caddy 0640 в private TLS directory.
   Никаких tls internal/certificate overrides/warning bypass. Lifetime около
   160h: renewal timer минимум каждые 12h, deploy hook копирует PEM и reload-ит
   только Caddy. Проверить renewal dry run, reload, alert при expiry<48h.
   Первый certificate сам по себе не доказывает работоспособность продления.
7. Настроить closed/auth/admin env только из локальных secret channels.
   Владелец выполняет interactive Linux admin_bootstrap и подтверждает TOTP;
   пароль/provisioning URI в чат не передаются. Настройка существующего бота
   не включает изменение webhook или Serverless modules.
8. Проверить loopback dependencies ready; public /ready/internal закрыт;
   certificate chain/IP SAN/expiry доверены обычным клиентом; user не получает
   admin; restart API/DB/host сохраняет synthetic acceptance data. Использовать
   отдельную disposable acceptance DB, не удалять ledger rows ради cleanup.
   Реальных пользователей не импортировать до auth relay/backup acceptance.

## Риски и rollback

Малый RAM, disk/WAL exhaustion, SSH/firewall error, ACME/renewal failure,
публичный auth ingress, runtime secrets, same-provider outage. Daily VPN,
off-host backup/restore/scheduler и Telegram relay этим rollout не закрываются.
Clock/NTP baseline нужен до постоянного lease, одного synced flag недостаточно.

Сохранить sanitised baseline units/firewall/ports и encrypted backup любых
обнаруженных важных данных. При ошибке stop/disable **только новые** detourvpn/
Caddy units, удалить **только добавленные** 80/443 rules, вернуть прежний proxy
config, если был. Данные/volume/keys сохранить, `down -v`, destructive downgrade
и массовое удаление не применять. Пакеты/каталоги удалять отдельным audit cleanup.
CA account/certificate не исчезают при rollback: отзыв/закрытие — отдельное
согласованное внешнее действие. Никаких DDoS/provider independence обещаний.

## HTTPS без домена: источники, не live evidence

Владелец сообщил, что своего домена нет. Это не разрешение его регистрировать.
LE подтверждает IP certificates/shortlived 160h; Certbot >=5.4 — IP/webroot.
Caddy читает explicit PEM paths; его automatic IP issuance здесь не предполагается.

- https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability
- https://letsencrypt.org/2026/03/11/shorter-certs-certbot
- https://caddyserver.com/docs/caddyfile/directives/tls

TLS templates ещё не проверены установленным Caddy. Реального выпуска/renewal
на наших IP не было; это остаётся частью отдельно одобренного rollout.

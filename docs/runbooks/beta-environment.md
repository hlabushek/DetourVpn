# Бесплатная закрытая beta — среда и операционные ворота

Статус: частичный hosted rollout 2026-10-26. `infra/beta/` развернута только на
двух отдельных beta VMs и не заменяет временный staging A или control plane B.
API/DB остаются loopback-only; Caddy отдаёт challenge/redirect и закрытый HTTPS
404; один VPN slot работает с закрытым gate, node-agent не запущен. Решение:
ADR-045; issue: I-030.

## Локальная проверка templates

Из корня репозитория:

```powershell
docker compose -f infra/beta/compose.yaml config --quiet
uv run --frozen pytest infra/ansible/tests/test_beta_config.py -q
uv run --frozen pytest infra/ansible/tests/test_beta_node.py -q
uv run --frozen pytest infra/ansible/tests/test_beta_node_config.py -q
uv run --frozen pytest infra/ansible/tests/test_beta_node_package.py -q
```

Реальный локальный packet-path test запускается только в Kali WSL из отдельных
mount и network namespaces (под root внутри WSL). Он генерирует TEST ONLY ключи
в `/run` tmpfs и проверяет pinned AWG handshake, HTTP через синтетический veth
uplink, slot/host NAT и DNAT, блокировку private host address, закрытый nft gate
после проверки и teardown. Ни один veth не соединён с физическим интерфейсом;
systemd start/stop заменён TEST ONLY controller. Harness сам откажет без
внешних изоляционных namespaces:

```bash
# From the mounted checkout, as root in Kali WSL with the pinned node Python environment.
/usr/bin/unshare --mount --net /path/to/node-venv/bin/python scripts/check_beta_slot_bootstrap.py
```

На target beta node bootstrap требует один default egress route, напрямую
назначенный ему публичный IPv4 endpoint и уже включённый
`net.ipv4.ip_forward=1`. Bootstrap не меняет global sysctl. Администратор
выделенного host должен настроить и сохранить forwarding до запуска сервиса;
один локальный smoke test этого требования не удовлетворяет. Node bootstrap
откажет при конфликте `10.203.82.0/26`, host route/address или beta nft table.
Поддержка provider NAT и multihomed uplink не предусмотрена. См. ADR-052.

Локально собрать только allowlisted node source bundle и проверить pinned binary
hashes можно так (укажи новый, ещё не существующий output path):

```powershell
uv run --frozen python scripts/package_beta_node.py --output-dir .cache/beta-node-v3-YYYYMMDD
```

Сборщик не включает node config, credential или slot keys и маркирует manifest
`deployable=false`. Bundle не устанавливает зависимости, systemd unit, AWG
слоты, маршруты, NAT или firewall.

Offline-компилятор также создаёт закрытые `node/awg/slot-N.conf`. На отдельном
Linux node их нужно вручную перенести в `/etc/detourvpn-node/awg/` вместе с
`node/slots.json`; bootstrap применяет их при старте, но не устанавливает файлы.
Подробности — ADR-049/051. Source JSON и
весь результат содержат секреты и должны оставаться вне checkout. В частности,
`api/window-manifest.json` тоже секретен из-за `header_protection_key`; он не
должен попадать в Git, чат или обычный артефакт сборки. CLI доступен только из
POSIX/Linux окружения, требует source file mode 0600 и приватный каталог
результата, не перезаписывает существующий output path. Он не устанавливает
сформированные файлы.

После подготовки source в собственном secret manager запускать его нужно из
Linux окружения с зависимостями API проекта, задав source/output пути вне
checkout. Пример команды без значений ключей:

```bash
uv run --frozen python scripts/prepare_beta_node_config.py \
  --source /root/.config/detourvpn-beta/slot-source.json \
  --output-dir /root/.config/detourvpn-beta/rendered-20261025
```

На выделенном Linux beta-host после отдельного разрешения также потребуются
`systemd-analyze verify` для units и `caddy validate` для IP, TLS paths и
challenge root. Agent unit разобран `systemd-analyze verify` в Kali WSL fixture
root; beta API host теперь прошёл `systemd-analyze verify` для certificate units
и `caddy validate` для закрытого HTTPS Caddyfile на Caddy 2.11.7. Внешние
проверки подтвердили HTTP-01, доверенный IP SAN, HTTPS 404 и staging renewal
dry-run. Future API-proxy Caddyfile с точным API path allowlist read-only
проверен на target Caddy 2.11.7; он не установлен. Активным остаётся
closed 404-only config, ingress не открыт. Текущая TLS/renewal приёмка: receipt
`docs/evidence/i-030-beta-api-tls-20261026.json`.

## Ежедневный backup I-027

Для бесплатной тестовой беты владелец выбрал локальную папку на своём ПК и
частоту около одного зашифрованного архива в сутки. S3 не используется.
`scripts/deploy_beta_local_backup.ps1 -RunHere` настроил backup-only systemd
service на изолированном beta API и задачу Windows `DetourVpn Beta Backup Daily`
на 07:00. Задача запускается от вошедшего владельца, использует pinned SSH
identity, скачивает только зашифрованный файл и проверяет его Fernet-аутентичность.
Локальные файлы находятся в `%LOCALAPPDATA%\DetourVpn\beta-backup`; ACL ограничены
текущим пользователем и SYSTEM.

Первый beta archive проверен и восстановлен из локальной копии в disposable
PostgreSQL; в beta DB данные не записывались. Состояние задачи:

```powershell
Get-ScheduledTask -TaskName 'DetourVpn Beta Backup Daily'
Get-ScheduledTaskInfo -TaskName 'DetourVpn Beta Backup Daily'
```

Ключ Fernet хранить отдельно от архивов и не помещать в репозиторий, чат,
журналы или общую папку синхронизации. Unit читает `VPN_DATABASE_URL` из
`/etc/detourvpn-beta/operations.env`, пишет только зашифрованный `latest.enc`.
Ключ хранится отдельно в `/etc/detourvpn-beta/backup-secrets` с ограниченными
правами; общий secrets-каталог не менялся.

Systemd timer template `infra/beta/detourvpn-beta-backup.timer` не включён:
расписание выполняет ПК владельца. Первый плановый запуск Windows-задачи ещё не
состоялся. `StartWhenAvailable` поможет выполнить пропущенный запуск, когда ПК
станет доступен и пользователь войдёт в Windows. Retention и автоматическое
удаление копий не настроены; независимого монитора/уведомлений нет.

## Изоляция и подготовка

1. Назначить новый host с persistent disk и отдельный VPN node. Не использовать
   A или B. До установки сохранить read-only baseline ОС, users, systemd,
   firewall, ports, Caddy, Docker, disk/RAM и существующих VPN services.
2. Передавать source по reviewed allowlist; не копировать `.env`, `.tgcloud`,
   БД/backup, APK, ключи или временные artifacts. Release root-owned, runtime
   user не может изменять code.
3. На новом host создать отдельного Linux user `detourvpn-beta`, root-private
   `/etc/detourvpn-beta` и независимые bootstrap/migrator/runtime passwords в
   локальном secret manager. Не помещать их в repo, shell history, process args,
   screenshots или чат. Compose bind-mounted secrets — файлы, не encrypted
   secrets manager; каталог и ACL должны ограничивать их чтение.
4. Использовать новый DB volume `beta_postgres_data`; init script допустим только
   для пустого volume. Не применять `compose down -v` и не удалять volume при
   ошибке. PostgreSQL/API остаются loopback-only на 25433/28000.
5. Выбрать разные постоянные публичные IPv4 для beta API host и beta VPN node.
   Подставить IP API host в `infra/beta/Caddyfile.ip-bootstrap` для HTTP-01,
   затем подставить IP API host и node в `infra/beta/Caddyfile.ip`. Не оставлять
   неразрешённые placeholders в `/etc/caddy/Caddyfile`; перед reload проверить
   полученный файл точной установленной версией Caddy. Не размещать
   недоверенный proxy перед Caddy: allowlist проверяет непосредственный source IP.
   Отдельно пройти HTTP-01 staging certificate.
   Доверенный Let's Encrypt IP certificate доступен с `shortlived` профилем,
   действует 160 часов и требует Certbot >=5.4 для webroot IP flow. До
   переключения HTTPS проверить renewal hook, автоматический timer и внешний
   монитор отсутствия успешного renew/check. Не включать API ingress до trusted
   TLS и проверки секретов/миграций.
   ADR-055 разрешает literal public IPv4 HTTPS origin только как локальное
   значение `miniAppUrl`; BotFather/Telegram webview acceptance для IP URL всё
   ещё обязательна. Не задавать URL или менять опубликованную конфигурацию бота
   до получения доверенного сертификата и отдельного scope на live acceptance.
   Фактическая установка на beta API VM: Caddy 2.11.7 + Certbot Snap 5.8.0.
   IP cert issuance, SAN, внешний TLS/404, renewal dry-run и custom systemd
   timer service проверены; snap-provided duplicate timer выключен. Отдельная
   expiry alerting система отсутствует, поэтому этот шаг не принимает I-027.
6. Для HTTPS API и Mini App использовать beta-only BotFather configuration.
   Не менять меню уже опубликованного бота без явного scope: URL кнопки глобален
   для пользователей этого бота. Не отправлять сообщения друзьям этим runbook.

## Что не даёт открыть beta

### Bootstrap beta admin до подключения Telegram token

На beta API VM предусмотрен отдельный `scripts/start_beta_admin_setup.ps1`.
Он проверяет закрытый admin-only режим, отсутствие user auth/Mini App/invite
gate и отсутствие существующей admin identity; затем запускает штатный
`scripts/admin_bootstrap.py` по SSH в видимом отдельном PowerShell окне.
Пользователь задаёт уникальный admin password и подтверждает TOTP enrollment
локально. Enrollment URI показывается только в этом окне, временный файл имеет
mode 0600 в `/run` и удаляется после успешного подтверждения. Этот шаг не
включает Telegram token, auth, invite gate или публичный ingress. Не записывать
экран и не переносить URI/password/TOTP code в чат.

После закрытого MFA bootstrap Bot API token для `@uysdgyfudf_bot` можно только
внести через `scripts/start_beta_bot_token_input.ps1`. Скрипт проверяет точный
beta host/режим, открывает отдельный PowerShell, скрыто принимает token и передаёт
его SSH stdin. Он атомарно добавляет только `VPN_TELEGRAM_BOT_TOKEN`, bot ID из
token prefix и подтверждённый username в root-only `/etc/detourvpn-beta/api.env`
(0600). Не использовать вместо него Telegram Serverless CLI Access token. Этот
шаг не перезапускает API и не включает auth/Mini App/invites/ingress; не вызывает
Bot API и не меняет BotFather. См. ADR-058. Если token нужно отменить, отозвать
его у BotFather; не ротировать/заменять значение скриптом автоматически.

Текущая beta host read-only inventory 2026-10-26: `environment=closed`,
`enable_admin=true`, `closed_admin_only=true`, user auth/Mini App/invite gate
выключены, Fernet admin key присутствует. Bot API token теперь staged в api.env;
API не перезапускался и runtime auth остаётся выключенной. Первичный
`owner` bootstrap завершился, но enrollment URI был отправлен в чат и считается
раскрытым; beta-only containment отключил эту identity, отозвал сессии и записал
audit event. Повторный bootstrap разрешён только как `beta-owner`, если в БД
ровно одна отключённая identity `owner`. Не использовать раскрытый seed.
Локальный admin/MFA/invite suite: **39 passed**, временная
PostgreSQL остановлена и удалена runner’ом. После enrollment API всё ещё будет
только loopback-ready, а HTTPS ingress останется закрытым 404.

Первоначальный TLS baseline отвечал HTTPS 404. Позже beta Mini App/auth allowlist
был установлен на beta Caddy 2.11.7, затем обновлён точным `/api/v1/referrals`
matcher. Внешний HTTPS проверен: user API без сессии возвращает 401, Mini App и
JS asset — 200, а readiness/internal unknown paths остаются закрытыми. См.
`../issues/I-030.md` и `../evidence/i-030-beta-auth-activation-20261026.json`.
Новая beta node имеет один закрытый slot и reboot acceptance. На node установлены
`agent.json` и credential с root-owned mode 0600; API manifest с публичной частью
slot key и `HeaderProtectionKey` установлен root-owned mode 0640. Одноразовый
credential source в `/run/detourvpn-beta/` удалён после проверки обеих сторон.
Позднее отдельный guarded runner активировал только beta API↔node control plane:
API readiness=200, DB node `active`, `VPN_ENABLE_WINDOW_TUNNELS=true`, manifest
подключён, node-agent, slot-bootstrap и core units `active`. В последней проверке
runtime был свежим, поступали аутентифицированные agent polls, активных peer
connections было 0. Это доказывает регистрацию и polling агента, но не работу
пользовательского туннеля. Provider UDP ingress, peer handshake и Android privacy
acceptance не приняты. Временный relay из I-028 имеет срок действия и не является
persistent beta ingress.

Read-only inventory нашла четыре старых каталогa
`/opt/detourvpn-node/payload-*` с root-owned файлами mode `0666` и каталогами
`0777`. До включения агента подтверждены отсутствие ссылок из systemd/cron,
открытых файловых дескрипторов и вложенных mount points. Содержимое сохранено;
права закрыты до root-only: файлы 0600, каталоги 0700. После изменения
bootstrap/core оставались active; после последующего activation gate node-agent
также active.

Первичная выкладка `NodeRegistry` и beta provisioning CLI выполнена на beta API
host 2026-10-26; API readiness вернулась в 200 после restart. Первый CLI запуск
отказал до БД из-за локального `public_origin=https://127.0.0.1`; после замены
проверки на точный beta DB target (`detourvpn_beta`, `vpn_api`, loopback:25433)
helper был обновлён атомарно без API restart. Rollback-копия:
`provision_beta_node.py.pre-beta-db-target-ed1242c19e19453e912f5d2f77db8375`.
Disabled node создан; credential и manifest теперь staged и проверены на двух
beta hosts. Локальный операторский инструмент `scripts/bind_beta_node_config.py`
при необходимости повторного аудита можно запускать только после проверки кода и
receipt; одноразовый source credential уже удалён.

`services/api/tests/test_nodes.py`: **4 passed** с временным PostgreSQL 18
кластером в Kali WSL; кластер автоматически удалён после тестов. Beta DB не
использовалась.

Не повторять provisioning с новым idempotency key для уже созданной beta node.
Credential не восстанавливается из хеша в БД. Node уже активирована; для
безопасного возврата к disabled состоянию использовать guarded rollback из
`scripts/activate_beta_node.py`. Он останавливает node-agent только при отсутствии
активных peer connections и восстанавливает API env из root-only backup.
Текущий rollback tag: `3f701cc95cd24e9598df406ef73dda5f`.

Команда из локального PowerShell в корне проекта:

```powershell
& '.\.venv\Scripts\python.exe' '.\scripts\activate_beta_node.py' --rollback-tag 3f701cc95cd24e9598df406ef73dda5f
```

Перед rollback runner повторно проверяет фиксированные beta DB/API/node targets,
нулевое число активных peer connections и целостность backup. Не редактировать
API env или DB вручную в обход runner.

Перед приглашением остаются I-026 end-to-end Telegram-issued `initData` → OTP
delivery → Android verify, I-027 ежедневная зашифрованная копия на ПК владельца,
передача и restore drill, I-028 comprehensive DNS/IPv6,
two-device, lifecycle и failure acceptance, а также peer handshake и проверка
пользовательского туннеля на beta node. Invite-only конфигурация и MFA admin уже
активированы; приглашать друзей до закрытия перечисленных gates нельзя.
Резервирование квот, agent polling или handshake сами по себе не доказывают
приватность.

Для одного owner-only smoke перед подключением доступен операторский helper
`scripts/start_beta_owner_test_credit.ps1`. Он через beta MFA admin API один раз
начисляет ровно 1 MiB только если в beta DB ровно один активный аккаунт и его
ledger полностью нулевой; затем сверяет ledger и завершает admin-сессию. Скрипт
не принимает Telegram ID, не печатает user ID/токен и безопасно отказывает при
неоднозначности или ненулевом остатке. Запуск из локального PowerShell открывает
отдельное окно; пароль и текущий TOTP вводятся только в скрытые prompt поля:

```powershell
& '.\scripts\start_beta_owner_test_credit.ps1'
```

Это разовый бесплатный beta credit, не платёж/подписка; он не истекает сам по
себе. Использовать его только для ограниченного smoke на выделенной beta node.
Перед дальнейшим owner testing остаток можно оставить в закрытой beta-среде;
друзьям доступ не выдавать до закрытия I-026/I-027/I-028.

Для продолжения остановившегося owner-only smoke подготовлен отдельный
`scripts/start_beta_owner_topup.ps1`: ровно один дополнительный 1 MiB credit.
Он требует ровно один активный beta аккаунт, исходный grant ровно 1 MiB,
ненулевой расход меньше 1 MiB и нулевой reserved balance. После начисления
проверяет grant 2 MiB, неизменный расход и отсутствие резерва. Повторный запуск
после успешного начисления откажет. Пароль и TOTP вводятся локально; B/production
и платежи не затрагиваются.

Этот ограниченный top-up выполнен 2026-10-26: beta grant total 2 MiB,
reserved balance 0. Ledger receipt:
`../evidence/i-030-beta-owner-topup-20261026.json`.

Для оставшихся коротких Android acceptance checks отдельный helper
`scripts/start_beta_owner_acceptance_credit.ps1` может добавить ровно 4 MiB
только если единственный активный аккаунт имеет grant ровно 2 MiB, ненулевой
расход меньше этого grant и нулевой резерв. Он сверяет итог 6 MiB, неизменный
расход и нулевой резерв; повторный запуск после успеха откажет. Пароль/TOTP
вводятся локально. Не использовать этот тестовый остаток для друзей или
чувствительного трафика. Такой top-up выполнен 2026-10-26; receipt:
`../evidence/i-030-beta-owner-acceptance-credit-20261026.json`.

Пока privacy acceptance не завершена, не рекомендовать друзьям подключать
чувствительный трафик. Kill switch/Always-on не заявлять до принятия Android
Lockdown и безопасного восстановления.

Изменения production/B, DNS/аккаунтов, платежей и внешняя публикация остаются за
пределами этого beta runbook и требуют отдельного явного решения. B/production не
меняются.

Последний owner-only Android smoke (2026-10-26) и ошибка fresh OTP session
записаны в `../evidence/i-030-beta-android-owner-smoke-20261026.json` и issue
I-030. После входа обновлённое приложение автоматически связывает локальный
device public key перед сверкой старых окон; не очищать данные приложения и не
запускать дополнительный tunnel без конкретной гипотезы. Текущий экран после
проверки: VPN выключен, осталось 337868 байт. Ограниченный ping/DNS smoke не
заменяет I-028 acceptance и не разрешает приглашать друзей.

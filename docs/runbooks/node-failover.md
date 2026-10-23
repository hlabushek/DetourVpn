# I-019 — выбор узла и bounded recovery

Полный physical drill принят 2026-10-23 в scope ADR-033: два сервера одного
провайдера, 15/15 facts, ledger и cleanup passed. Текущие receipts:
`docs/evidence/i-019-staging.json`, `docs/evidence/i-019-session-post-cleanup.json`.
Оба временных grant удалены; повторять команды для завершения этого этапа
не требуется. Инструкции ниже предназначены для будущего отдельно разрешённого
drill. Независимость от провайдера остаётся открытой.

## Локальное воспроизведение

```powershell
uv run --frozen ruff format --check services/api
uv run --frozen ruff check services/api
uv run --frozen python -m mypy
uv run --frozen python scripts/export_openapi.py --check
$env:PYTEST_ADDOPTS = '-q --tb=short -k "node_failover or operations or tunnel"'
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
Remove-Item Env:PYTEST_ADDOPTS
.\scripts\build_flutter_client.ps1
```

PostgreSQL runner создаёт случайный локальный cluster/БД и удаляет их после
остановки. Node operations/Telegram delivery в tests — TEST ONLY synthetic;
реальные внешние endpoint не вызываются. Android runner собирает обычные
debug и unsigned release, выполняет Flutter tests, native unit и lint.

## Операторская конфигурация (только после разрешения staging)

`VPN_TUNNEL_PROFILE_FILE` сохраняет старый одиночный формат. Новый формат:
объект `version: 1`, `profiles: [profileA, profileB]`. Каждый profile содержит
`node_id`, `runtime_id`, `server_public_key`, `header_protection_key`, `endpoint`.
Node UUID, endpoint и server public key должны различаться. Endpoint — глобальный
числовой IPv4:UDP-port, без URL/DNS/private addresses; 1–65535, без ведущих нулей.
Default endpoint сохранён только для legacy single-profile совместимости;
для второго узла оператор обязан явно указать его одобренный endpoint.
Меняя server identity, используйте новый runtime UUID и завершайте старые leases.

Профиль содержит секрет: хранить **вне checkout**, POSIX mode 0600; Windows ACL
только владельцу и процессу API. Не вставлять содержимое в чат, shell args,
screenshots или evidence. Не создавать demo secrets. API читает максимум 8192 bytes,
отвергает symlink, неверную version, неизвестные поля и дубли; при ошибке весь
manifest fail closed. Агент B использует свой node credential и runtime, отдельно
от A. Существующие I-011/I-012 staging playbooks жёстко ограничены прежним host:
не менять inventory в обход guards и не считать их готовым multi-host deployment.
Установка B требует отдельного проверенного baseline и ограниченного provisioning.
Backend по-прежнему не имеет generic remote shell endpoint.

### Размещение pinned artifacts на B (ADR-033)

`scripts/prepare_i019_node_b.ps1 -SyntaxCheck` проверяет синтаксис без SSH.
`scripts/prepare_i019_node_b.ps1` запускается владельцем в локальном PowerShell;
на `BECOME password:` вводится sudo-пароль vpn-deploy для B, скрыто и только
локально. Этот шаг размещает лишь прежние проверенные бинарники/лицензии
и root-owned release symlink. Туннель, NAT, forwarding, firewall и службы
не запускаются и не настраиваются.

После `Missing sudo password` (ok=9, changed=0, failed=1) подтверждено,
что release B отсутствует и forwarding остался 0. Причина отсутствующего
ввода по этому сообщению не установлена. Launcher теперь выделяет Linux PTY
через `script --echo never`, проверяет stdin/stdout TTY перед Ansible и
направляет output log в `/dev/null`; input/timing logging не включается.
Это проверка терминального транспорта, не доказательство успешного sudo.

Используется отдельный `infra/ansible/inventory.i019-b.yml`. B разрешён в
artifact playbook только при явном `vpn_i019_second_node_approved=true`, точных
адресе/inventory name/user; фактический IPv4 сверяется с host facts.
Без флага отказ проверен до remote setup (changed=0); syntax check A/B прошёл.
Host-key checking включён; в WSL копируется только ранее доверенная публичная
запись B из Windows known_hosts. Ключи хоста через keyscan не принимаются.
I-011/I-012 runtime guards не расширены. Успешное размещение artifacts не
является доказательством VPN и не делает старый lifecycle runner двухузловым.

B сообщает `ip_forward=0` и `ens3` как default interface. Для будущего drill
нужно отдельно учесть временное forwarding и его подтверждённое восстановление;
этот artifact step его не меняет. При неудаче сохранить task/recap и сначала
разобрать причину; не запускать повторно вслепую.

`GET /api/v1/nodes` требует `tunnels:manage`; Mini App получает 403. Каталог
возвращает configured registry rows: id/name/region/reason и checked_at.
`available` = active + negotiated v2/runtime + heartbeat <=10s + свободный slot.
Один узел может обслуживать лишь один bounded peer. Это не egress/latency probe.
Нет profile — пустой каталог, прежний admission_pending режим без config.

`POST /api/v1/tunnels` принимает optional node_id. Без него выбирается первый
доступный profile в operator order; с ним отказ не заменяет выбор молча.
Тот же idempotency key повторяется с тем же intent при потере ответа. Новый
intent нельзя создавать на том же устройстве, пока старый не revoked.

## Потеря узла A

1. В Android нажать «Отключить VPN»: local native выключается до сетевого запроса.
2. При доступном A дождаться removal ACK и settlement по final counters.
3. Если A потерян, дождаться deadline+10s. Кнопка «Завершить истёкший lease со
   списанием резерва» явно вызывает `POST /tunnels/{id}/recover`. Это требует
   owner session и stale heartbeat (>10s). Ранний запрос возвращает стабильный
   `tunnel_recovery_not_ready` 409. Можно безопасно повторить.
4. При отсутствии final counters списывается **весь исходный резерв**: обычный
   клиент запрашивает 1 048 576 bytes. observed_bytes остаётся NULL, поэтому это
   не проверенный расход и не событие referral qualification. Версия peer
   увеличена; старый ACK не меняет новый lease. Recovery не изображает ACK.
5. После revoked обновить каталог, выбрать B и нажать «Подключить». Используются
   свежая proof и новый idempotency key. Никакого автоматического reconnect нет.

При ошибке API клиент сохраняет старый intent. Перезапуск приложения не является
процедурой recovery; прежний restart/resume scope I-013 не расширен этим issue.
Недостаток квоты после консервативного списания честно запрещает новый lease.
Нет обещания бесшовного переноса/сохранения текущих TCP sessions.

## Обязательный live gate — пока не выполнен

Для двух конкретных staging hosts отдельно согласовать адреса, права, бюджет,
временное окно, команды отключения A и cleanup. До действия проверить baseline,
kernel gates, runtime/key identity, отсутствие чужих peers/контейнеров, часы
(погрешность node/API не более 1s, gate operation не более 5s),
SSH transport API. Production root credentials не нужны.

По ADR-033 владелец разрешил этот drill на A 45.10.166.106 и B 85.209.132.205
у одного провайдера. Независимость от отказа провайдера проверяется позднее
отдельно; её нельзя включать в успешные результаты этого drill.
Read-only preflight 2026-10-22: SSH/TUN/NTP доступны; B без pinned release;
на обоих требуется локальная интерактивная sudo-авторизация, adb не видит
телефон. Это предварительные сведения, не полный privileged network baseline.
Старые A-only runners на B не запускать путём подмены inventory/guards.

- На физическом Android подтвердить A: handshake и свежий egress probe.
- В согласованном окне остановить только выделенный A runtime/agent. Проверить
  traffic cutoff, kernel expiry без control plane, local disconnect/обычную сеть.
- До deadline recovery должен отказать, новый peer на том же device запрещён.
- После deadline+10s выполнить recovery: один debit, reserved release, audit
  consistent; повторить запрос и проверить неизменность.
- Подключить B на том же device: B agent ACK, native handshake, свежий egress
  и DNS/IPv6/network-switch матрица по отдельным требованиям I-011/I-013.
- Возвратить A: старый peer не оживает, late ACK не меняет ledger/B. Мониторинг
  видит действительные stalled commands, игнорирует superseded versions.
- Revoke B, проверить final counters/ledger, возврат обычной сети, удалить
  temporary peer/config/forwarding/grants и подтвердить baseline обоих hosts.

В evidence писать только codes/counts/timestamps/хеши, без конфигов, ключей,
credential, Telegram payload или сетевого содержимого. До такого receipt I-019
не считается принятой на реальных двух VPN узлах. Коммерческий запуск no-go.

## I-019: текущий отдельный runner

1. `scripts/run_i019_staging.ps1 -SyntaxCheck` — без VPS изменений.
2. `scripts/run_i019_staging.ps1 -BuildOnly` — format/analyze/Flutter tests,
   native unit/lint/debug/unsigned release и компиляция отдельного physical
   entrypoint. ASCII snapshot вне OneDrive, private ACL и source hash stamp.
   Этот шаг уже прошёл; неизменённые исходники повторно не пересобираются.
3. В своём локальном PowerShell выполнить `scripts/run_i019_staging.ps1 -Prepare`.
   Перед запросами sudo выполняется read-only проверка часов A/B/API (<=1s);
   при отказе новые grants не устанавливаются. Source stamp нормализует пути
   и использует ordinal sort, одинаковый для Windows PowerShell 5 и PowerShell 7.
   Последовательно запросит sudo `vpn-deploy` для **A**, затем **B** в отдельных
   PTY с выключенным echo и output log `/dev/null`. Пароли в чат/args/files
   не передавать. Устанавливает только фиксированный 45m grant и units;
   VPN endpoint ещё не запускается. Существующие grants/markers не перезаписывает.
4. `scripts/run_i019_staging.ps1 -UseExistingGrants` — opt-in реальный drill.
   Нужен один разблокированный физический arm64 Android по USB. Test APK
   устанавливается в прежний debug package, VPN appop временно разрешается.
   Подробный сценарий — ADR-033. API и peer profiles локальные/временные,
   Telegram delivery — TEST FAKE ONLY. Это не production API/Telegram тест.
   Сначала проверяется B handshake/egress и штатное отключение. При отказе B
   длительные A expiry/failure окна не повторяются. Затем полный A→B drill;
   ledger проверяет четыре settlement (два B observed, два A counters_lost).
   Текущий physical entrypoint запрашивает по 16 MiB на lease при общем балансе 64 MiB;
   обычный клиент сохраняет default 1 MiB. Egress требует два свежих IP ответа
   выбранного узла подряд; не более трёх проб, другой IP — немедленный отказ.
5. Проверить `docs/evidence/i-019-staging.json`: passed и cleanup обоих hosts,
   source/clock/ledger facts, force-stop приложения, appop reset, удаление adb
   reverse и private profile manifest. PostgreSQL runner удаляет свой cluster.
   При failed/cleanup_not_confirmed сначала диагностика сохранённых fixed codes,
   без verbose/raw config и без слепого повторного запуска.
   При phone failure доступны whitelist `phone_failure` (catalog/admission/
   handshake/egress, phase/error, bounded native metadata), `failure_control_plane`
   (heartbeat/tunnel states/ACK flags) и fixed-action `failure_nodes` перед cleanup.

Локальная реальная проверка cleanup пустых nft-таблиц (без VPS, egress и ключей):

```powershell
wsl -d kali-linux -u root --cd (Get-Location).Path -- /usr/bin/unshare --net -- /usr/bin/python3 scripts/check_i019_nft.py
```

Helper отказывается работать вне свежего WSL network namespace и требует
пустой исходный nft ruleset. Использует реальные nft-объекты формы стандартных
iptables tables; сам iptables frontend локально не проверялся (не установлен).

### Однократный ввод паролей для диагностической сессии

Владелец запускает `scripts/run_i019_staging.ps1 -Prepare -DiagnosticSession`
в своём PowerShell и вводит sudo A, затем B. После этого Codex использует
`-UseExistingGrants -DiagnosticSession`. До следующей попытки Codex разбирает
`docs/evidence/i-019-progress.json` и отдельный `i-019-attempt-<id>.json`, проверяет
runtime cleanup и вносит/проверяет необходимые локальные исправления. Клиент
пересобирается через `-BuildOnly`, если его исходники изменились.

При неудаче удаляется runtime, но остаётся grant до исходного 45m deadline;
таймер не продлевается. Успех полного drill удаляет grant автоматически.
Codex завершает оставшуюся сессию командой `-FinishSession` без участия владельца;
она не зависит от телефона, актуальности APK или предыдущего PostgreSQL.
Проверить `i-019-session-cleanup.json`; ошибка первого хоста не отменяет попытку
очистки второго. При истечении таймера действуют прежние автоматические stop/expire.
Редактирование закреплённого root-кода на хостах требует новой подготовки.

Короткая проверка B: перед `-UseExistingGrants -DiagnosticSession` временно
установить `VPN_I019_B_REUSE_DIAGNOSTIC=true`, после вызова восстановить прежнее
значение env. Она запускает только B и сохраняет отдельный
`docs/evidence/i-019-b-reuse-diagnostic.json`, mode=b_reuse_diagnostic_only.
Это два настоящих lease/revoke/settlement в одном runtime, без ожиданий отказа A;
её успех не закрывает полный двухузловой gate. В diagnostic session исходные
grants сохраняются даже после успеха короткой проверки. Для полного drill
переменная должна отсутствовать или иметь значение, отличное от `true`.

Если изменён только physical integration target, Codex может выполнить
`uv run --frozen python scripts/build_i019_physical_incremental.py`: он сначала
проверяет исходный build stamp с предыдущим cached target, затем format/analyze,
Flutter tests и новый physical APK. При изменении production inputs отказ;
тогда нужен обычный `-BuildOnly`. Нельзя вручную переписывать stamp для обхода.
Весь namespace уничтожается при выходе. Synthetic OS/command tests явно помечены
в `infra/ansible/tests/test_i019_control.py` и не считаются live VPN evidence.

### Первый live запуск: остановка до endpoint start

Receipt `i-019-staging-failure-20261022-clock.json`: run остановился на clock
preflight, до запуска VPN. Оба grant отозваны, owned resources отсутствуют,
forwarding и телефон не менялись; временный PostgreSQL остановлен/удалён.
Readonly диагностика: A offset interval 0.942–0.998s, B 0.935–1.012s; при
допуске 1s B нельзя считать подтверждённым. Порог не ослаблялся.
Владелец выполнил штатный `w32tm /resync` в локальном admin PowerShell;
новые измеренные upper bounds: A 0.0836s, B 0.0918s. Перед следующим run
измерение повторяется. Сетевые ограничения/runtime gates не обходились.
Clock subprocess pipes теперь закрываются явно на success/failure; четыре
synthetic unit tests проверяют границу <=1s, отказ >1s и закрытие handles.

# I-012: финальная bounded staging приёмка

Текущий код: [ADR-024](../adr/024-bounded-tunnel-admission.md).
Текущие результаты: [verification](../verification-i-012-admission.md).
**Технический I-012 принят 2026-10-20 в bounded staging scope.**
Все три сценария и cleanup подтверждены
[`i-012-staging-lifecycle.json`](../evidence/i-012-staging-lifecycle.json).
Команда ниже предназначена для осознанного повторного воспроизведения;
для текущего закрытия повтор не требуется.

## Один запуск владельца

Подключить уже авторизованный Pixel по USB, разблокировать экран, отключить
другие VPN на телефоне. В PowerShell из корня проекта выполнить:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_i012_staging.ps1
```

На `BECOME password` ввести **sudo-пароль staging пользователя vpn-deploy**
только в локальном терминале. Не отправлять его в чат. Production/root SSH
credentials не требуются. Прежний временный grant уже отозван (ADR-021);
новый код не может воспользоваться старым разрешением. Этот запуск — явное
одобрение только указанного bounded test на прежнем согласованном staging.

Скрипт последовательно:

0. Проверяет ровно один ADB status `device` до сборки и перед установкой.
   Настраивает приватную папку до sudo: записывает только DACL без SACL/owner
   изменений и без запуска Windows от администратора. Каталог должен принадлежать
   текущему пользователю и не быть ссылкой.
1. Собирает Android POC/test APK, запускает unit tests/lint в strict dependency
   verification mode. Тестовый transport-код есть только в instrumentation APK.
   До sudo также запускает evidence/control regression tests и настоящий
   Android↔host control IPC preflight: намеренно пропускает transient phase и
   проверяет доставку сохранённого события/локального ACK. Он не вызывает SSH,
   не меняет VPN и не является remote restart acceptance. После него test APK
   удаляется, POC останавливается; live test устанавливает test APK заново.
2. Через существующий non-root SSH устанавливает проверенный fixed-action
   controller и agent unit **только на согласованном staging**. Lease привязана
   к UID/host/boot, wall+monotonic clocks и hashes. Sudoers проверяется visudo.
   Grant истекает через 45m, каждый endpoint/agent — не позднее 600s. Boot enable
   и изменение existing VPN containers отсутствуют.
3. Создаёт отдельный локальный PostgreSQL, synthetic accounts и API на loopback.
   **Telegram delivery — TEST FAKE ONLY**; реальный Telegram не подключается.
   Сессии, API, ledger, node auth, агент, native VPN и телефон в live тесте настоящие.
   Node credentials/profile/временная SSH copy находятся в user-only ACL directory
   `LocalAppData/ProjectVpn/i012-private`, вне OneDrive checkout.
4. Соединяет agent→API через SSH reverse loopback; телефон→API — через ADB reverse.
   Не открывает публичный plaintext API. Проверяет на физическом телефоне:
   API create → proof ключа → node ACK → config → handshake/новый egress;
   API revoke с прекращением трафика; фактический restart агента с reconciliation;
   отключение control-plane SSH transport до kernel expiry и восстановление для
   settlement. Повторы/replay и kernel budget дополнительно проверены локально.
   Каждый synthetic account получает 16 MiB, live request резервирует 16 MiB
   в пределах ADR-024; lease остаётся 60s. API default 1 MiB не меняется.
5. Останавливает agent/endpoint, проверяет **все семь** baseline restored checks
   актуального InvocationID, отзывает grant и останавливает его expiry timer,
   удаляет runtime credentials/profile, instrumentation APK, ADB/SSH forwarding,
   останавливает POC/API и уничтожает только свой disposable PostgreSQL.
   Исходный SSH key в WSL не изменяется; временная Windows copy удаляется.

Для выполнения нужен уже подготовленный WSL controller
`/tmp/vpn-ansible-2.21.5`, portable PostgreSQL в `.cache/i-004/portable/pgsql/bin`,
прежний scoped SSH key/known_hosts и private inventory/release files. Скрипт
не устанавливает отсутствующие инструменты и не выбирает другой сервер.
При ошибке не повторять вслепую: сохранить имя failed task/recap либо sanitized
evidence. Не включать `-vvv`, не отправлять configs, пароли, токены или raw journal.
При `agent_negotiation` смотреть только allowlisted `agent_failure_stage` в failure
receipt. До phone сценария harness должен увидеть реальную v2 negotiation.
При отказе телефона смотреть `phone_failure.stage`, booleans interface/permission,
HTTP status и `lease_diagnostics` с целочисленными counters. Это минимум для
различения UI, native start, handshake/egress и исчерпания бюджета; raw logs не нужны.
Revoke/restart считаются проверенными только при cutoff до natural lease expiry.
`revoke_precondition` означает, что active/time budget ещё не подтверждены;
`waiting_revoke_ack`, `native_state_at_cutoff`, `traffic_cutoff`,
`cutoff_before_expiry`, `quota_settlement` и `network_restore` отделяют причины.
`egress_result` — только unavailable/staging/other, сам пользовательский IP
не сохраняется. HTTPS probe использует новый platform TLS factory на каждом
запросе, сохраняя стандартную проверку сертификата/имени.
После native disconnect test до 20s ожидает teardown маршрутов/DNS и свежий
обычный HTTPS ответ. Native DOWN сам по себе восстановлением сети не считается.
Локальный `localStartup` также проверяет этот полный UP/DOWN/restore цикл.
Новая версия требует повторной установки endpoint script, agent и unit: старые
grants не подходят, поскольку проверяются hashes. Права Windows администратора
для запуска не нужны.
При потере связи независимые kernel/600s/45m ограничения остаются; это не замена
фактической последующей проверке cleanup.

Успех создаёт `docs/evidence/i-012-staging-lifecycle.json` с `passed: true`.
Ошибка сохраняется отдельным датированным failure JSON; старый успех не является
доказательством нового запуска. До проверки нового receipt I-012 остаётся открытым.

## Контракт и локальная разработка

Только test APK поддерживает instrumentation flag `-e localStartup true`:
два запуска после `adb shell am force-stop org.example.vpnpoc` проверяют
поднятие/очистку native интерфейса с loopback peer без сервера. После проверки
нужно force-stop POC и удалить `org.example.vpnpoc.test`. Это не VPN connectivity
test; весь трафик при кратком UP blackhole-ится локально. Release APK этого режима
не содержит. Требуются разблокированный телефон и отключённые другие VPN.
Флаг `-e freshEgress true` выполняет только две свежие HTTPS пробы по обычной
сети, без VPN/node изменений и без вывода actual IP. После него также остановить
POC и удалить test APK. Он не является staging/VPN acceptance.
Флаг `-e controlEvent true` используется `scripts/check_i012_control_events.py`:
проверяет persistent control events независимо от UI phase. Live parent исполняет
только restart_agent/pause_control/restore_control, однократно; актуальный
phase больше не является одноразовым управляющим сигналом.

Локальный reproduction для отдельной systemd mount namespace границы (без WAN):

```powershell
wsl -d kali-linux -u root --cd (Get-Location).Path -- python3 scripts/i012_systemd_acceptance.py
```

Тест использует только свои transient units, private `/run` directory и namespace;
отказывается при конфликте имён, ограничен runtime и убирает свои ресурсы.
Запускать только в подготовленном локальном WSL с pinned I-010 artifacts.

После локального сбоя до начала test допускается `-UseExistingGrant` только
для продолжения с уже выданным действующим разрешением и готовыми APK.
Этот режим не обновляет grant и не меняет его hashes/срок: реальный controller
проверяет разрешение до старта. После cleanup или изменения controller требуется
обычный запуск с новым локальным sudo. Не использовать для слепого повтора.

Миграции: 0007 tunnels/outbox и 0008 key proofs/reservations/runtime. Для своей
dev DB применять вручную после backup: `uv run --frozen alembic upgrade head`,
затем `uv run --frozen alembic check`. Runner выше не меняет существующую dev DB.
Downgrade с proof/reservation/lease/receipt data запрещён; force удаления нет.

Без `VPN_TUNNEL_PROFILE_FILE` admission default закрыт: create возвращает
202 `admission_pending`, без peer/config. Trusted private profile — node UUID,
runtime UUID, server public key, HeaderProtectionKey и фиксированный endpoint
из I-011. Server private key остаётся на стенде. Production profile policy и
secret-manager deployment принадлежат следующему эксплуатационному этапу.

При включённом staging profile:

- `POST /api/v1/tunnels/key-challenge/{device_id}` требует owner и bound session;
  возвращает ephemeral public key/transcript/UUID, TTL 120s. Клиент вычисляет HMAC
  через upstream X25519; private device key не отправляется.
- `POST /api/v1/tunnels`: device_id, idempotency_key, proof_id, proof_response,
  budget_bytes (65536–16777216, default 1048576). Owner/session/device/proof,
  quota, fresh node capability/runtime и one-peer capacity проверяются атомарно.
  Ответ 202 `provision_pending`. Повтор возвращает тот же ID/текущее состояние.
- `GET /api/v1/tunnels/{id}/config`: только session своего device, после applied
  ACK и до expiry. Иначе `tunnel_config_not_ready`/409. Ответ no-store, без private key.
  `active` означает applied peer; handshake доказывается отдельно на телефоне.
- List/read/delete только своего объекта. Чужой/unknown одинаковый 404.
  Delete сохраняет новую desired version/remove; до ACK — `revoke_pending`.
  Device revoke делает это в той же transaction и закрывает сессии.
- Quota API: `available = granted - used - reserved`. Финальный native total
  сохраняется отдельно, charged ограничен budget; missing counters → полный
  budget. Это явная sandbox policy ADR-024, не production тариф/обещание wire billing.
- Agent v2 `/api/internal/v1/agent/peers/lifecycle/{poll,ack}` использует credential,
  timestamp/nonce, capabilities `peer-lease.v2`/`kernel-udp-budget.v1`, observed
  inventory, runtime UUID, command UUID и peer version. Nonce/state commit атомарны.
  Потерянное подтверждение не сбрасывает kernel budget. Restart не восстанавливает add.

Типичные stable codes: `device_key_proof_required`, `device_key_proof_invalid`,
`device_key_unusable`, `insufficient_quota`, `tunnel_node_capacity`,
`tunnel_node_unavailable`, `tunnel_config_not_ready`, `peer_version_conflict`,
`peer_lease_expired`, `agent_capability_mismatch`, `agent_runtime_mismatch`.
Validation inputs/secret profile не включаются в ошибки или HTTP access logs.

## Повторяемые проверки без VPS

```powershell
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen python scripts/export_openapi.py --check
uv run --frozen python scripts/export_usage_contracts.py --check
uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
wsl -d kali-linux -u root --cd (Get-Location).Path -- python3 scripts/i012_kernel_acceptance.py
wsl -d kali-linux -u root --cd (Get-Location).Path -- python3 scripts/i012_native_acceptance.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_i012_staging.ps1 -SyntaxCheck
```

Linux scripts создают только собственные LOCAL WSL namespaces, проверяют hashes
pinned binaries и cleanup/root routes. Никакого WAN/VPS в этих двух tests нет.
Обычный suite пропускает только opt-in live тест; это не положительный live результат.

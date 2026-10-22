# I-012: admission, native daemon и финальный live gate

2026-10-19–20, ветка `codex/i-012-tunnel-lifecycle`.
**I-012 технически принят 2026-10-20 в ограниченном staging scope ADR-024.**
Все три сценария прошли на настоящем VPS и физическом Pixel с реальными API,
PostgreSQL и node agent. Канонический результат:
[`i-012-staging-lifecycle.json`](evidence/i-012-staging-lifecycle.json), `passed=true`.
Telegram delivery в harness — **TEST FAKE ONLY**.

## Финальная физическая приёмка

| Сценарий | Фактический результат |
|---|---|
| Выдача подключения и отзыв через API | Proof принят, конфиг после ACK узла, свежий handshake и staging egress; отзыв прекращает трафик |
| Настоящий перезапуск удалённого агента | Перезапуск подтверждён отдельно; reconciliation прекращает трафик до естественного истечения lease |
| Потеря канала управления | Канал отключён при ещё активной lease; kernel expiry прекращает трафик, канал затем восстановлен |
| Учёт и телефон | Во всех трёх сценариях settlement ровно один раз, после отключения восстанавливается обычная сеть |
| Очистка | Все семь baseline checks восстановлены, grant удалён; дополнительно проверено отсутствие работающих тестовых служб, таймера, API/БД, ADB forwarding и временных credentials |

Владелец ввёл sudo-пароль локально; дальнейший прогон и проверка завершены
автоматически. Повторный запуск для закрытия issue не требуется. Scope: один
peer, lease до 60s, тестовый budget 16 MiB; production admission не включался.
Поле `egress_verified=false` в событии готовности endpoint — прежняя диагностика
endpoint harness, а не итог телефонной приёмки: фактический staging egress
подтверждён отдельно в каждом из трёх `scenarios`.

## Реализованный путь

Auth session → регистрация client public key → ephemeral X25519 challenge →
одноразовое подтверждение владения ключом → атомарные quota reservation и
durable add → node capability/runtime/inventory checks → native peer add с
kernel budget/timeout → applied ACK → выдача конфига своей bound session.
Private device key остаётся в памяти телефона. API не объявляет connected:
`active` означает подтверждённое применение, handshake измеряется клиентом.

Tunnel/device revoke сохраняет новую desired version и remove. До node ACK —
`revoke_pending`; после удаления/финального snapshot — однократный settlement
и `revoked`. При restart агент закрывает gate, удаляет peers, сохраняет counters
и tombstone; повтор add не восстанавливает доступ и не сбрасывает budget.
Новый runtime/отсутствующий observed peer приводит к remove/reconciliation.

Scope ADR-024: один peer на выделенный staging endpoint, max lease 60s,
budget 64 KiB–16 MiB. Без trusted profile admission default закрыт. Нет
автоматического renewal, fleet или production secret-manager deployment.
Расход после потери counters консервативно равен budget; native observed total
сохраняется отдельно, charge capped reservation. Это явная sandbox policy.

## Изменённые файлы продолжения

- `services/api/src/vpn_api/tunnels/admission.py`, `lifecycle.py`, `contracts.py`,
  `routes.py`, `service.py`: proof, profile, admission/config, v2 protocol и settlement.
- `services/api/src/vpn_api/db/models.py`, migration
  `0008_tunnel_admission.py`: proofs, reservations, runtime/capability metadata,
  constraints/indexes, guarded downgrade и immutable receipt reservation snapshot.
- `services/api/src/vpn_api/quota/{contracts,service}.py`, `config.py`: reserved
  balance, корректные ledger/reconciliation snapshots и opt-in private profile path.
- `services/api/src/vpn_api/usage/service.py`: отказ второго accounting binding
  для managed lease; source `tunnel_lease` отдельно от I-008 `usage` и sample audit.
- `services/node-agent/agent.py`: fixed native UAPI/nft operations, protected
  persistent journal, one-shot kernel gate, inventory и startup cleanup.
- Android `KeyConfirmation.java`, `KeyConfirmationTest.java`,
  `PocLifecycleTests.java`, `PocDeviceTests.java`: upstream crypto/JCE и test-APK
  сценарии. API transport orchestration не входит в release APK.
- `services/api/tests/test_tunnel_admission.py`, `test_node_daemon.py`,
  `test_i012_live.py`; updates `test_migrations.py`, `test_quota.py`,
  `test_quota_api.py`, `test_tunnels.py`: positive/negative/regression coverage.
- `scripts/i012_{kernel_acceptance,native_acceptance,control}.py`,
  `scripts/run_i012_staging.ps1`, `infra/ansible/staging-tunnel-lifecycle.yml`,
  `infra/ansible/files/project-vpn-i012-agent.service`,
  `infra/ansible/tests/test_i012_control.py`: реальный local lab и один staging runner.
- `pyproject.toml`, `uv.lock`, `scripts/export_usage_contracts.py`, OpenAPI и
  `packages/contracts/peer-lifecycle-*.v2.json`: pinned PyCA dependency и schemas.
- ADR-024, security-review-i-012, runbooks/quota/tunnel-lifecycle-staging,
  README/ROADMAP/issue/status docs: policy, воспроизведение и ограничения.

Первая поставка 0007 и её файлы/354 tests описаны отдельно в историческом
`verification-i-012.md`; это продолжение той же issue, не I-013.

## Фактические результаты

| Проверка | Результат |
|---|---|
| Formatter, Ruff | Passed |
| Strict mypy | Passed, 80 source files |
| Отдельный mypy для нового infrastructure test | Passed |
| uv lock --check | Passed, 76 locked packages |
| OpenAPI + internal usage/peer schema checks | Current |
| Последний полный runner на disposable настоящем PostgreSQL | **386 passed, 1 skipped**, 188.48s |
| Controller tests | **13 passed**, включены в последний полный прогон |
| Current collection | 391 tests; полный baseline и новые проверки запускались отдельно, без заявления о новом едином полном прогоне |
| Evidence/control + daemon + controller regressions | **32 passed**, включая 4 новых evidence/control tests |
| Opt-in live acceptance | **Passed**, все три сценария на VPS/Pixel, реальный restart и потеря/восстановление канала управления |
| Android strict dependency build, app lint/debug/release + test APK, tunnel release | BUILD SUCCESSFUL |
| Android JVM unit tests | **10 passed**, 0 failures/errors |
| pip-audit locked requirements с --require-hashes | No known vulnerabilities found |
| Ansible `run_i012_staging.ps1 -SyntaxCheck` | Passed, без подключения/установки на VPS |
| Local WSL systemd unit verify | Exit 0; предупреждения о NTFS source permissions, staging copy задаёт 0644 |

Полный runner проверил миграцию с пустой БД, repeat upgrade, Alembic schema drift,
пустой rollback/reupgrade, отказ downgrade с данными и concurrent writer. Proof
tests используют настоящую PyCA crypto, PostgreSQL и API; node ACK в этих tests
**synthetic**, не реальный узел. Test daemon native backend отдельно явно fake;
эти unit tests не выдаются за сетевой результат.

Первый расширенный полный прогон: 365 passed, 1 failed, 1 skipped — старый exact
quota response expectation не включал новый reserved_bytes. Ожидание исправлено;
следующие полные прогоны прошли. Последний включает отдельный ledger source
`tunnel_lease`, запрет двойного I-008 binding, общую usage/ledger audit при hold
и после settlement, и конфликт изменённого lost-counter ACK. Ранее исправлены лабораторные адреса и сброс
client session между разными lease, чтобы local native сценарий соответствовал
новому подключению. Неудачные попытки не считаются приёмкой.

## Настоящий Linux/native результат

Команды выполнялись **только в LOCAL WSL**, без WAN/VPS:

```powershell
wsl -d kali-linux -u root --cd (Get-Location).Path -- python3 scripts/i012_kernel_acceptance.py
wsl -d kali-linux -u root --cd (Get-Location).Path -- python3 scripts/i012_native_acceptance.py
```

Первый test подтвердил общий byte budget и timeout без работающего agent loop.
Второй запустил два настоящих pinned AWG peers и настоящий agent executor,
подтвердил handshake и rx/tx, remove с прекращением ping, отказ старого add,
kernel expiry при оставшемся native peer и budget cutoff. После каждого прогона
свои namespaces/processes/socket files убраны, host routes совпали с baseline.
Свежий результат привязан к SHA-256 agent source:
`docs/evidence/i-012-local-native.json`. Это **не** новый staging/phone результат.

## История отладки до успешной приёмки

Ниже сохранены промежуточные неудачи и исправления. Их незавершённые состояния
относятся к прошлым попыткам; текущий итог — успешная приёмка выше.

### Фактический прогон 2026-10-20

#### Потерянный сигнал перезапуска в test orchestration

Последний live receipt подтвердил полностью успешный api_revoke, включая
cutoff, settlement и network restore. Во втором сценарии телефон оставался
active при `waiting_revoke_ack`; console не содержит actual agent restarted.
Причина находится в test orchestration: transient `waiting_agent_restart`
немедленно перезаписывался `waiting_revoke_ack`, а parent опрашивал только
текущую phase раз в 250ms. Такой же lost-event риск был для restore transport.
Эта regression внесена при детализации checkpoint phases; она не является
доказательством поломки restart handler самого node daemon.

В test APK commands теперь сохраняются в persistent-in-scenario `control_events`
отдельно от phase. Parent принимает только три fixed события, выполняет каждое
однократно после успешного callback и не теряет их при запоздалом чтении.
Passed receipt дополнительно требует actual_agent_restart_verified=true.
Перед pause transport требуется активная lease с запасом >5s до deadline,
чтобы не объявить outage после естественного cutoff доказательством offline gate.

Regression tests подтвердили late poll/перезаписанную phase, однократность,
late restore и отказ неизвестных операций. На настоящем Pixel отдельный
`controlEvent` instrumentation намеренно сразу меняет phase, а host читает с
задержкой и подтверждает доставку private-file ACK. Он прошёл: это **только
локальный Android↔host IPC**, не remote restart и не VPN acceptance.
Новый `scripts/check_i012_control_events.py` выполняет его автоматически в
обычном runner **до sudo/Ansible**; malformed signal теперь остановит запуск
до изменения VPS. На этапе preflight удаляется только test APK/свой ACK,
POC остаётся остановленным. Никаких SSH/privilege операций в preflight нет.

Android build/lint/10 JVM tests прошли (19s); Ruff/mypy (80 files) и 32 targeted
tests прошли. Server code/capabilities/grant lifetime не менялись. Три общих live
сценария ещё не приняты; old partial receipt не подменяет успешный общий gate.

#### Последний отказ: восстановление обычной сети

Следующий датированный failure receipt с фазой `network_restore` подтвердил
native UP, staging egress/handshake, active/time precondition перед DELETE,
revoke_pending → revoked, cutoff до natural expiry и settlement 248736 bytes.
По порядку checkpoints также пройдены quota checks и native disconnect;
отказ произошёл на единственной немедленной HTTPS проверке обычной сети.
Общая issue остаётся открытой, restart/expiry ещё не приняты.

В test APK добавлен `awaitNetworkRestore`: до 20s по monotonic clock ожидает
native DOWN и новую нормально проверенную HTTPS пробу с обычным egress.
DOWN нативного backend не гарантирует синхронный teardown Android VpnService,
маршрутов и DNS. Проверка по-прежнему не принимает staging egress после disconnect.
Allowlist теперь сохраняет уже пройденные cutoff/quota/restore booleans в failure.

Два последовательных fresh-process физических localStartup цикла на Pixel
подтвердили native UP → DOWN → свежий HTTPS обычной сети. Local peer только
loopback без VPN сервера: это физический UI/native teardown test, **не staging**.
Android build/lint/10 JVM tests и 30 targeted Python tests прошли; Ruff/mypy passed.
Телефонные always-on/lockdown настройки прочитаны как null/0 и не менялись.
Node/API contracts, staged root code и пределы lease/budget не менялись.
Нужен новый единый live receipt трёх сценариев; старые partial результаты
не подменяют его. Повторная общая regression не запускалась: изменены только
test APK и фильтрация evidence, релевантные проверки выполнены отдельно.

#### Последний повтор с budget 16 MiB

Receipt `docs/evidence/i-012-staging-lifecycle-failure-20261020-191654.json`
подтверждает real node negotiation, proof/config, native UP, staging egress и
свежий handshake. Отзыв дошёл до node ACK/`revoked`; settlement сохранил
**103984 observed/charged bytes из 16777216**, то есть budget не исчерпан.
Phone остановился в старой объединённой фазе `api_revoke`, поэтому нельзя
объявить успешной проверку прекращения трафика или назвать точную причину.
Все семь restored=true, grant удалён; обе службы позже проверены как не активные.

В test APK исправлены неоднозначности самой проверки: каждый HTTPS probe
получает отдельный platform TLS context/factory и закрывает response stream.
Default trust managers и hostname verification сохранены. Отдельный factory
участвует в [ключе HTTP connection pool Android](https://android.googlesource.com/platform/external/okhttp/+/refs/heads/oreo-release/okhttp/src/main/java/com/squareup/okhttp/Address.java),
что исключает reuse соединения из предыдущего состояния сети в этой реализации.
Это исправление измерения, а не доказанная причина staging отказа.

Перед API revoke теперь требуется active и не менее 15s до lease deadline.
DELETE допускает только revoke_pending/revoked; затем отдельно проверяются ACK,
сохранение native UP, отсутствие нового HTTPS-трафика и запас до natural expiry.
Последнее требуется как для revoke, так и для agent restart, чтобы не принять
kernel timeout за успешное удаление peer. Добавлены отдельные phases и bounded
state/egress classification: unavailable/staging/other, без actual IP в evidence.

Две real physical HTTPS пробы без VPN/staging прошли: `freshEgress` test flag,
HTTP 200, scope отдельно зафиксирован в `i-012-android-https-probe.json`.
Android build/lint/10 JVM tests прошли, BUILD SUCCESSFUL за 23s.
Ruff/mypy и 30 targeted tests (включая фильтрацию wrong types/секретных labels)
прошли. Контракты/API/node code не менялись; новый полный live gate ещё нужен.

#### Первый частичный успех

`docs/evidence/i-012-staging-lifecycle-failure-20261020-185455.json` подтверждает
настоящую v2 negotiation и успешный **api_revoke** на Pixel: proof, config after
node ACK, native UP/staging egress, свежий handshake, API revoke/cutoff,
settlement и восстановление обычной сети. Telegram delivery остаётся TEST FAKE ONLY.
Namespace fix, таким образом, проверен и на staging, но вся issue ещё открыта.

Второй сценарий получил applied config, но остановился на прежней широкой метке
`native_connect`, которая включала UI/native UP/egress/handshake. Agent restart
ещё не запускался, expiry scenario не выполнялся. Причина этого отказа пока
**не установлена**. Семь restored=true, cleanup и grant removal подтверждены;
read-only SSH проверка после прогона: endpoint/agent не активны, grant отсутствует.

Новая instrumentation разделяет profile parse, activity/permission, native start,
native UP, staging egress и handshake; сохраняет безопасные boolean/status facts.
Старый api_error очищается после успешного HTTP ответа, поэтому предыдущий
409 config_not_ready больше не выглядит текущей ошибкой после HTTP 200.
Python harness дополнительно allowlist-фильтрует phone_failure и сохраняет
state/budget/charged/observed/settled bytes до удаления disposable DB.
Два tests проверили отбрасывание посторонних данных/labels/wrong types.
Вместе с daemon/controller: **30 passed**; Ruff/format/mypy passed.
Последний Android build: **BUILD SUCCESSFUL**, 16s, 205 tasks; JVM tests 10/0/0.

На физическом Pixel два последовательных fresh-process local startup checks
подтвердили UI button, действующее разрешение и native interface UP. Peer имеет
только loopback endpoint без сервера: это **не handshake/VPN/egress evidence**.
Первый диагностический local profile с loopback DNS был недопустим для Android;
после исправления только тестового DNS на TEST-NET оба startup checks прошли.
Этот первоначальный local отказ не считается объяснением реального staging отказа.

Только live acceptance request теперь резервирует разрешённые ADR-024 **16 MiB**
вместо 1 MiB: полный IPv4 маршрут также переносит background traffic телефона.
Это запас тестового бюджета, а не доказанная причина/исправление предыдущего
сбоя. Max lease 60s, kernel enforcement, API default 1 MiB и production policy
не менялись. В synthetic account 16 MiB доступны до выдачи; все три сценария
должны пройти заново в одном чистом запуске с cleanup.

### Предыдущие прогоны 2026-10-19

Последний live прогон дошёл до `physical_phone_api_revoke`, но остановился на
`tunnel_request`: node daemon завершился до negotiation, peer не был выдан.
Failure receipt от 20:36 UTC содержит новый InvocationID, все семь restored=true,
cleanup_complete=true и control_grant_removed=true. Это не VPN acceptance.

Причина воспроизведена и исправлена отдельным настоящим systemd тестом:
namespace mount endpoint unit не виден второй unit; прежний named path оказался
placeholder. Теперь root-owned readiness хранит PID/inode, agent проверяет root,
точный cgroup и core hash, закрепляет namespace fd, исключает host namespace
и передаёт fd только fixed nft child. Подробности/основание в ADR-024.

`scripts/i012_systemd_acceptance.py` подтвердил исходный отказ, новый pinned fd,
kernel timeout и работу второй unit с прежними sandbox restrictions. Все семь
флагов true, cleanup/routes сохранены: `docs/evidence/i-012-local-systemd.json`.
`scripts/i012_native_acceptance.py` теперь тоже использует namespace fd;
повторный реальный AWG прогон подтвердил все восемь флагов, включая handshake,
пакеты, remove/replay/expiry/budget и cleanup: `i-012-local-native.json`.
Оба receipt относятся **только к LOCAL WSL**, привязаны к новому agent hash.

Добавлены семь synthetic proc identity tests (success и fail-closed варианты),
включённые в 386 passed. Android rebuild прошёл за 32s, 205 tasks, 10 JVM tests.
Harness теперь ждёт реальную v2 negotiation до телефона, собирает только
allowlisted stage/API error/status и не пишет passed receipt при исключении
после сценариев или неподтверждённом control-plane loss/recovery.
Новые hashes требуют обычной установки с локальным sudo перед повторной
физической приёмкой. На VPS read-only проверены nsenter, cgroup v2 и system.slice;
обе тестовые службы не активны, grant и временная Windows SSH copy удалены.

Повторная попытка 2026-10-19 выявила ошибку Windows `Set-Acl` (запрос
SeSecurityPrivilege). Runner теперь записывает только новый DACL до sudo;
владелец не меняется, итоговая ACL проверена: только текущий пользователь,
inheritance отключено. Windows PowerShell 5 выполнил исправленный путь без
повышения привилегий; PowerShell/Ansible syntax check прошёл.

Продолжение с действующим grant дошло до `prepare`, где file-only legacy
validator ошибочно применялся к runtime directory. Заменён отдельной проверкой
root ownership, directory type и отсутствия group/other write на всём пути.
13 controller tests прошли (включая положительный prepare, wrong owner,
writable directory, file/symlink); Ruff/format/mypy этих файлов прошли.
В этом прогоне peer не применялся; failure receipt
`docs/evidence/i-012-staging-lifecycle-failure-20261019-202726.json` содержит
cleanup_complete=true/control_grant_removed=true. Это не live acceptance.

Для намеренного повторного воспроизведения владелец запускает одну команду из
`docs/runbooks/tunnel-lifecycle-staging.md`, вводит sudo-пароль локально и держит
Pixel разблокированным с выключенными другими VPN. Скрипт автоматически выполнит:

1. Реальный API create/proof/ACK/config → Android handshake/новый egress → API revoke/cutoff.
2. Реальный restart daemon → inventory reconciliation/remove/cutoff до natural lease expiry.
3. Потеря control-plane forwarding → kernel expiry/cutoff → восстановление/settlement.
4. Root cleanup с семью baseline checks, отмена expiry timer и grant; cleanup
   телефона, SSH/ADB/API, private files и disposable PostgreSQL.

Успешный receipt: `docs/evidence/i-012-staging-lifecycle.json`, новый timestamp,
`passed=true`, три сценария, control-plane recovery и cleanup/grant flags.
Без такого receipt issue не объявляется завершённой. Исходный WSL SSH key
не меняется; временная Windows copy защищена user-only ACL вне OneDrive и
удаляется. Secrets/raw configs никогда не нужно присылать в чат.

## Риски и оставшиеся общие gates

Локальный отдельный review: `docs/security-review-i-012.md`. Это review
исполнителя, не независимый аудит. Production retention/deletion, публичный TLS
ingress, node credential rotation, secret manager, полный licence/SBOM и внешний
security review остаются release gates. DNS/IPv6/Doze/все Android network paths
новым кодом не объявляются полностью измеренными. Telegram delivery в harness
явно TEST FAKE ONLY; VPN/native/HTTP/ledger в успешном opt-in live gate реальны.
Flutter UI и renewal не реализованы этой issue. Ни remote CI, ни публикации,
ни production deploy этим отчётом не подтверждаются.

I-012 закрыт в границах ADR-024; issue и ROADMAP обновлены. Рекомендуемый
следующий этап — I-013 по отдельному заданию. Он этим прогоном не начинался.

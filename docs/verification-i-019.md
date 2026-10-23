# I-019 — проверка multi-node selection / bounded recovery

Обновлено: 2026-10-23. Ветка: codex/i-019-node-failover. ADR-031/ADR-033.

## Текущий результат: полный physical staging drill passed

Attempt `14652957080243ddab229e9cf37b59d8`, mode `full_two_host_failover`:
`1 passed, 552 deselected in 238.22s`. Receipt `docs/evidence/i-019-staging.json`
содержит passed=true и все 15 facts. Настоящие A/B, physical Android, PostgreSQL,
AWG, commands/ACK и HTTPS egress; только Telegram delivery — TEST FAKE ONLY.

По два свежих HTTPS ответа подтвердили B, A, повторный A и B после отказа A.
Проверены запреты раннего recovery и второго peer, kernel expiry при frozen
agent и недоступном API, cutoff до deadline при остановке A, два recovery/replay,
возврат A без оживления старого peer и обычная Android NOT_VPN/VALIDATED сеть.
Ledger: четыре settlement, два полных lost-counter debit по 16777216 bytes,
reserved_bytes=0, available_bytes=33300232 из initial=67108864. Реальные B ACK:
четыре delivery / четыре applied ACK. Clock upper bounds: A 0.199s, B 0.182s.

Root cleanup обоих узлов: семь baseline checks, forwarding restored,
owned resources absent, control grants removed. Phone stopped, reverse removed,
временный PostgreSQL остановлен и cluster удалён. Независимая readonly проверка
2026-10-23T07:24Z: agents/timers inactive, host links отсутствуют, forwarding
A=1/B=0, reverse/private manifest отсутствуют; passed=true в
`docs/evidence/i-019-session-post-cleanup.json`. Window unit ActiveState=failed
после намеренного аварийного завершения; активного runtime нет.

I-019 принят в согласованном same-provider node-failure scope ADR-033.
Provider independence остаётся открытой; automatic/seamless failover,
DNS/IPv6 leaks, переключения Wi-Fi/mobile, production и legal/payment acceptance
этим тестом не подтверждены. Повторный ручной запуск для этого этапа не нужен.
Ниже сохранена история локальных проверок и предыдущих неудачных попыток;
их отрицательные receipts не заменяют финальный полный результат.

## Первоначальная локальная граница результата (2026-10-22)

Реализована локальная часть I-019: два явно настроенных профиля, каталог
id/name/region/reason/time, выбор в Flutter и POST intent, отдельная конфигурация
и agent authentication для каждого узла. Узел проверяется повторно после slot
lock; прежний один peer на узел и один живой lease на устройство сохранены.

Owner recovery разрешён только после deadline+10s и stale heartbeat. В одной
транзакции полный reservation становится debit с прежним key=tunnel UUID,
observed_bytes=NULL, tunnel revoked и новой peer_version. Это не measured usage
и не removal ACK. Старые commands не применяются; мониторинг учитывает только
current peer version живых туннелей. Referral qualification требует observed
counters, поэтому потерянные счётчики её не дают.

Flutter отключает native перед recovery, сохраняет intent при отказе/потере
ответа, блокирует смену узла при живом intent, показывает полную цену recovery.
Каталог не выключает существующее подключение и не доказывает VPN reachability.
На API outage сохраняется прежняя политика ограниченного native lease.

Нет миграций и новых зависимостей; native parser/engine/kernel не менялись.
Никакой VPS/Telegram/DNS/покупки/production операции не выполнялось. Телефон
не перепрошивался/не переустанавливался; новая physical VPN acceptance отсутствует.

## Файлы

- `services/api/src/vpn_api/nodes/catalog.py`, `tunnels/{admission,contracts,service,routes}.py`,
  `main.py`, `operations.py`, `packages/contracts/openapi.json`.
- `services/api/tests/test_node_failover.py`, обновлённые контрактные assertions
  `test_tunnels.py`; прежние node/tunnel/operations/rewards/referrals tests.
- `apps/client/lib/{connection,main}.dart`, `test/node_failover_test.dart`,
  credential-free `test/ui_preview_test.dart`.
- ADR-031, issue/runbook/verification/evidence I-019, README, ROADMAP.

Все файлы checkout до I-019 были untracked; создана отдельная ветка, массовый
initial commit/staging чужих исходников и публикация не выполнялись.

## Проверки

Количества и SHA256 сборок: `docs/evidence/i-019-local-acceptance.json`.

- Python: `uv run --frozen ruff format --check services/api`, Ruff check и
  `uv run --frozen python -m mypy` (105 files) — passed.
- OpenAPI export и `scripts/export_openapi.py --check` — current.
- PostgreSQL: полный disposable regression и отдельные I-019 отрицательные/
  concurrency тесты. Node transport и Telegram — явно TEST ONLY synthetic.
  Финальный полный regression после обновления контрактных assertions:
  **506 passed, 3 skipped, 289.19s**. Skips: opt-in I-012 live, I-013 physical,
  Mini App browser QA; эти проверки в I-019 не запускались.
  После этого grace recovery увеличен с 5 до 10s с документированным staging
  clock bound; добавлена проверка отказа внутри grace. Финальный затронутый набор
  node_failover/operations/tunnel: **63 passed, 446 deselected, 59.81s**.
  Оба PostgreSQL runner остановили и удалили собственные generated clusters.
- Flutter: format/analyze, 44 tests включая шесть I-019 tests. Narrow 320px,
  text scale 2 и recovery warning без overflow. Light/dark изображения реальных
  widgets с TEST ONLY nodes просмотрены визуально.
- Android: обычные main.dart debug/unsigned release, native unit и lint через
  существующий `scripts/build_flutter_client.ps1`.
  Финальный runner: analyze no issues (6.8s), **44 Flutter tests passed**,
  Gradle **BUILD SUCCESSFUL (50s)**, **10 native tests, 0 failures/errors**,
  debug и unsigned release APK (43.2s для release).
  В packaged Dart assets debug/release отсутствуют выбранные test fake markers;
  AWG core присутствует для arm64-v8a/x86_64. Это ограниченная archive проверка,
  не supply-chain audit, security review, signing или публичный выпуск.

Первый PostgreSQL regression: 504 passed, 2 failed, 3 skipped. Два failures —
прежние assertions, запрещавшие новое node_id: ожидаемый набор response fields
и extra-field rejection. Обновлены под новый контракт; неверный UUID по-прежнему
422, изменение node_id повторного intent — 409. Первый новый Flutter widget test
поймал собственный незакрытый timer; timer явно остановлен, повтор прошёл.
Tooling warnings закреплённых Gradle/AGP/Kotlin/NDK не скрывались и не обходились.

## Ограничения / следующая работа

I-019 **не закрыт как live failover**: второй реальный узел и node-failure drill
не выполнены. Полный резерв может списываться при counters_lost: это сохранённая
bounded policy I-012, не политика готового коммерческого тарифа. Нет бесшовной
миграции TCP sessions, capacity scaling, автоматического переподключения и
восстановления intent после перезапуска приложения. Прежние physical/production
gates I-013/I-016/I-017 также не закрываются этими локальными тестами.

Следующий шаг — согласованный двухузловой staging drill по
`docs/runbooks/node-failover.md`, включая clocks/kernel expiry, fresh egress,
cutoff, ledger и cleanup. Нужен второй разрешённый staging host и отдельное
одобрение ограниченных infrastructure изменений по AGENTS.md. I-020 (payment
sandbox) здесь не начинается и дополнительно зависит от legal gate.

## Продолжение: подготовка двух существующих servers, ADR-033

2026-10-22 владелец разрешил A/B одного провайдера. B artifacts размещены
локальной owner-run командой: ok=14, changed=3, unreachable=0, failed=0.
Четыре удалённых SHA-256 и root ownership проверены независимо. Forwarding B
остался 0; I-011/I-012 службы inactive. Это установка artifacts, не VPN.

Подготовлены отдельный fixed-action I-019 controller, grant playbook, local
runner и physical Flutter entrypoint. Локально: 151 tests passed, 1 opt-in
live skipped; mypy 108 files, Ruff и PowerShell/Ansible syntax passed.
Flutter analyze без замечаний, 47 tests passed; native 10 tests, 0 failures/errors;
lint/debug/unsigned release и physical target build passed. Существующие
upstream Gradle/AGP/Kotlin/NDK warnings сохранены; validation не обходилась.
В fresh WSL network namespace реальные nft table cleanup/foreign-rule refusal
прошли; локального iptables frontend нет, его работа этим не проверялась.
Readonly SSH clock round trips дали upper bounds 0.9518s (A), 0.9336s (B)
относительно local API clock; перед live проверка повторяется и >1s запрещает run.

Полный перечень файлов и SHA-256: `docs/evidence/i-019-drill-preparation.json`.
Временные I-019 grants ещё требуют локального sudo; physical live drill пока
не выполнен. Для этой итерации нет нового утверждения passed live failover.

Первый opt-in run после owner preparation дошёл до real PostgreSQL и остановился
на `staging_clock_bound_exceeded` до endpoints. На обоих хостах cleanup_complete,
owned_resources_absent, forwarding_restored и control_grant_removed — true;
PG остановлен/удалён. Сохранён отдельный failure receipt. Windows clock
синхронизирован владельцем, новые read-only bounds 0.0836/0.0918s проходят
прежний 1s gate. Исправлен PS5/PS7 culture-dependent source stamp (ordinal sort;
одинаковый digest подтверждён обеими версиями) и закрытие clock pipes.
Добавлены 4 synthetic clock boundary/cleanup tests; mypy — 109 files passed.
Следующий запуск требует свежих временных grants после их корректного отзыва.

## Первый physical two-host run после синхронизации часов

Receipt `docs/evidence/i-019-staging-failure-20261022-b-connect.json` сохраняет
неудачный реальный прогон отдельно от будущих запусков. Clock bounds A/B:
0.0582/0.0466s. На физическом Android подтверждены A handshake/egress, запрет
раннего recovery и второго peer, kernel expiry при остановленном агенте и
недоступном API, восстановление обычной сети, recovery с replay, повторное
подключение A и cutoff при остановке runtime до deadline. На `b_connect`
тест остановился; handshake/egress B, возвращение A при активном B и итоговый
ledger не подтверждены. Cleanup обоих хостов: семь baseline checks,
forwarding_restored, owned_resources_absent и control_grant_removed — true.
Телефон остановлен, adb reverse удалён, disposable PostgreSQL удалён.

Отдельный непривилегированный UDP echo diagnostic на B:51893 прошёл с Windows
и физического Android (`i-019-b-udp-diagnostic.json`). Это только доступность
UDP на обычной сети; причиной отказа VPN оно не является и VPN не доказывает.

Для следующего прогона test-only entrypoint сначала проверяет B handshake/egress
и штатное отключение; длительные A expiry/failure окна выполняются после этого.
Итоговый ledger ожидает четыре settlement: два B с измеренными counters и два A
с counters_lost/full debit. Этап connect разбит на catalog/admission/handshake/egress.
Test-only loopback failure route сохраняет только whitelist значений, bounded
native metadata, heartbeat age, tunnel states и ACK booleans; произвольные
строки, конфигурации, ключи и auth данные отбрасываются. Перед cleanup доступны
существующие фиксированные status/diagnose/gate действия. Production API не менялся.

Проверки после этого изменения: 28 targeted Python tests passed (clock,
controller, diagnostic allowlist); Ruff passed, mypy 110 source files passed.
`run_i019_staging.ps1 -BuildOnly`: format/analyze без замечаний, 47 Flutter tests
passed, Android native unit/lint, обычные debug/unsigned release и отдельный
physical entrypoint APK успешно собраны. Новый physical run ещё не выполнялся:
после предыдущего cleanup необходимы свежие owner-run временные grants.

## Режим одной диагностической сессии

По прямой просьбе владельца добавлен `-DiagnosticSession`: при неудаче runtime
останавливаются с обязательной проверкой cleanup/forwarding/owned resources и
inactive services, исходные grants сохраняются без продления 45m deadline.
Успех полного drill отзывает grants; `-FinishSession` позволяет Codex независимо
очистить оба хоста без актуального APK, телефона и предыдущей БД. Прежние root
source/unit hashes и expiry timer не менялись. Исправления root-кода и истечение
срока по-прежнему потребуют owner-run подготовки.

В test-only entrypoint опубликованы текущие фиксированные этапы и whitelist
native metadata; progress JSON обновляется атомарно, каждому attempt соответствует
отдельный receipt. Ошибка cleanup не маркируется как остановленный runtime.
35 targeted Python tests passed, включая 7 новых synthetic session cleanup tests;
Ruff passed, mypy 111 source files passed, PowerShell parser passed.
Это локальная проверка режима сессии: нового утверждения о live B/failover нет.
Полный `-BuildOnly` после изменения режима: Dart format/analyze без замечаний,
47 Flutter tests passed, 10 Android native tests с 0 failures/errors, lint,
обычные debug/unsigned release APK и physical test APK успешно собраны.

## Diagnostic-session attempt e9c09fe0e25b4f8097f857999bd62021

На физическом Android прошли начальные B handshake/egress и штатное отключение,
оба A expiry/failure сценария, recovery/replay. После отказа A B снова получил
active peer, свежий handshake (age 5s, native up/network true, remaining 54275ms).
Его heartbeat age 0.158s, add_peer ACK подтверждён, runtime/agent active и kernel
gate открыт. Отказ локализован до `b_connect_egress`: одиночная HTTPS-проба не
подтвердила IP B. Её точный сетевой код прежний тест не сохранял; причина отказа
этой пробы пока не установлена. Это не полный passed failover.

Оба runtime очищены, forwarding и семь baseline checks восстановлены,
owned resources отсутствуют. Grants сохранены в пределах исходного deadline;
телефон/reverse/PG очищены. Отдельный attempt receipt сохранён автоматически.
Следующая версия теста требует два fresh HTTPS совпадения подряд из максимум
трёх проб и сохраняет whitelist результата/HTTP status/OS error. Ответ с другим
IP сразу завершает тест ошибкой. Локально 36 targeted tests passed, Ruff passed,
mypy 111 source files passed. Root-код/units не менялись, новых паролей не нужно.

Следующие диагностические attempts: 680746a4c8fc40fa9392bc42f28c82d3 и
25617153e6194d68a83071b1c7854a43. В обоих первый B egress получил IP B,
следующие пробы дали socket_error на request при свежем handshake. В последнем
test-only failure route выполнил штатный public DELETE и дождался реального
remove_peer ACK: budget=1048576, observed_bytes=charged_bytes=995864. API не
подменял counters/ACK. Этот расход плюс outer UDP/AWG overhead согласуется с
исчерпанием kernel byte budget, а не с недоступностью B. Kernel quota напрямую
этим receipt не считана; причина выведена из aggregate counters и её кода.
Во всех attempts runtime cleanup/forwarding/owned resources подтверждены,
grants сохранены до прежнего deadline; PostgreSQL и телефон очищены.

Для physical test введён явный leaseBudgetBytes=4194304; default клиента
остаётся 1048576. Допустим прежний API диапазон 65536–16777216, некорректные
значения отклоняются до intent. Общий баланс теста остаётся 16777216, recovery
двух A списывает по 4194304; итоговая сверка ожидает реальные B counters.
Добавлены тесты default, границ и сохранения настроенного budget в replay.

## Закрытие первой диагностической сессии и готовность следующей

Попытка d2c1b82aaa2f45f2b2e0ab4e7d7263fa с budget 4 MiB подтвердила начальные
два egress B, два egress A, kernel expiry/recovery и повторные два A egress,
cutoff/restore/recovery. Остановилась на `b_connect_admission`: агент B имел
свежий heartbeat, но новые add/remove ACK отсутствовали, kernel gate закрыт.
Причина повторной выдачи B пока не установлена; новый wrapper наблюдает реальные
commands/ACK в test-only API без подмены их содержимого.

Следующая попытка 86d65a3943524a0a9286aafb3148c32a прошла начальные B/A egress
и первое expiry/recovery, но остановилась на `restart_a_egress`: первый ответ A,
затем timeout/socket_error при свежем handshake. До повторной выдачи B не дошла.
Локально реальный isolated nft initialize/open/close/open прошёл; он не доказывает
повторное применение peer на remote AWG.

Перед истечением grant Codex вызвал FinishSession. Receipt
`i-019-session-cleanup.json`: оба runtime очищены, семь baseline checks прошли,
forwarding restored, owned resources absent, оба grant удалены. Независимый
readonly post-check `i-019-session-post-cleanup.json` тоже passed: agents/timers
inactive, host link отсутствует, forwarding A=1/B=0, reverse/profile удалены.
Последний full live receipt остаётся passed=false; I-019 не закрыт.

Новый physical target (ещё не проверен live): 16 MiB/lease, общий баланс 64 MiB,
default обычного клиента 1 MiB. Добавлен отдельный B reuse diagnostic с двумя
настоящими B lease/settlement и отдельным mode/receipt, без запуска A. Полный
drill по-прежнему требует все A/B facts, четыре settlement, два full lost-counter
debit и cleanup. Меньший режим не выдаётся за full acceptance.

Проверки текущей версии: 41 targeted Python tests passed (synthetic boundaries /
cleanup policy, не live), Ruff passed, mypy 112 files passed. Physical target
format/analyze, 49 Flutter tests и APK build passed. Incremental builder доказал
совпадение остальных source inputs с последним полным build перед компиляцией;
native 10 unit/lint и обычные debug/release APK ранее прошли полный runner,
их исходники в incremental шаге не менялись. Остались прежние tooling warnings.

## Продолжение 2026-10-23: короткая B диагностика

Новые grants выданы владельцем локально, сначала проверяется только B.
Attempt 4e6a9ba5f6684a0283fe2c40be03882e (mode=b_reuse_diagnostic_only) подтвердил
первые два B HTTPS egress и remove_peer ACK, native down и отсутствие lease.
Остановился на сравнении нормального public IP с исходным: HTTPS работал, но
возвращал другой IP. Readonly Android default-network metadata независимо
показали vpn=false, validated=true, not_vpn=true, normal=true. Результат остаётся
passed=false; второй B lease в этой попытке ещё не проверен, A не запускался.

Исправлена test-only restore проверка: native down + однозначная Android default
network NOT_VPN/VALIDATED без VPN transport + fresh HTTPS/IP вне A/B, без требования
неизменного public IP. Невалидный IP body и неизвестный/неоднозначный OS state
запрещают acceptance. Raw dumps/SSID/proxy/DNS/history не сохраняются.
43 targeted synthetic tests passed; Ruff passed, mypy 113 files passed.

Короткий physical B reuse diagnostic 46d56efcc3e44edfac37f753f09b235e прошёл:
`1 passed, 552 deselected in 60.51s`. Два настоящих B lease в одном runtime,
по два fresh HTTPS ответа B на каждый, четыре command delivery/ACK, оба revoke,
native down и Android NOT_VPN/VALIDATED после каждого disconnect. Ledger:
2 settlement, lost_counter_full_debits=0, reserved_bytes=0,
available_bytes=67024320 из initial=67108864 (debit sum 84544 bytes).
Семь B baseline checks, forwarding_restored и owned_resources_absent — true;
grant сохранён до исходного deadline. A не запускался. Receipt:
`docs/evidence/i-019-b-reuse-diagnostic.json`, mode=b_reuse_diagnostic_only.
Этот success не закрывает полный I-019; после него запущен отдельный полный drill.

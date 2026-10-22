# I-019 — проверка multi-node selection / bounded recovery

Дата: 2026-10-22. Ветка: codex/i-019-node-failover. ADR-031.

## Граница результата

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

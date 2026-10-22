# ADR-004: cumulative counters, epochs и связь с quota ledger

Status: **Proposed — local implementation for review**, 2026-10-18 / I-008.
Scope: trusted internal accounting engine. Настоящих nodes/agents/HTTP credentials
ещё нет: registry и authenticated transport — I-009/ADR-008. Этот код не принимает
счётчики от пользовательского приложения или анонимного HTTP caller.

## Identity, epoch и порядок

Trusted control-plane binding фиксирует (node UUID, peer UUID) → (device, user).
Owner выводится из device, не из sample JSON; composite FK сохраняет ownership.
Node UUID пока external accounting-source ID, не проверенная node identity и не
node registry. До реального ingest нужна независимая authenticated node principal;
нельзя брать её из того же недоверенного JSON. Локальный CLI требует DB access и
не заменяет service auth/mTLS. Public API не публикует agent/admin routes.

Counter epoch открывается отдельно trusted control action: UUID + started_at и
expected_generation (compare-and-set). Peer изначально/после реального reset имеет
нулевые counters. Первый report списывает rx+tx от нуля, а не получает бесплатный
baseline. Нельзя создать новый epoch одним sample или самопроизвольно принять reset.
Открытие следующего epoch завершает time interval предыдущего; старый не активируется
повтором open. Старые reports в своём interval по-прежнему можно принять с задержкой.
Начальные timestamps строго растут; reports имеют aware UTC timestamp внутри epoch,
не более 60 секунд в будущем относительно DB. Arrival delay не обнуляет counters.

Sample schema v1: peer_id, counter_epoch, sequence (1..BIGINT_MAX), sampled_at,
rx_total_bytes, tx_total_bytes, idempotency_key UUID. Целые неотрицательные bytes,
rx+tx <= BIGINT_MAX, никаких float/bool/URLs/IP/DNS/payload. Versioned JSON Schema
сохраняется отдельно от публичного OpenAPI.

Каждый epoch имеет два watermarks: observed (highest valid sequence/counters/time)
и charged counters. Higher sequence обязан иметь неубывающие rx/tx и sampled_at.
Меньший sequence с counters/time не выше observed — stale/no charge. Если он
противоречит observed — durable rejected. Уменьшение counters с новым sequence —
counter_regression, без отрицательного списания и без автоматического reset.
Одинаковые business (epoch, sequence) и key+payload возвращают тот же receipt;
изменённый payload/key или повтор key для другого report — conflict, без эффекта.

## Транзакция и недостаток квоты

Locks: User → stream → epoch; ledger повторно использует тот же User lock.
Sample receipt, quota receipt/debit и обновление watermarks коммитятся вместе.
Тарифицируется delta = (rx − charged_rx) + (tx − charged_tx), строго integer bytes.
Source quota operation — usage, identity/key генерируются сервером из sample ID.
Нулевой delta/stale/invalid report не создаёт фиктивной ledger debit.

Успешный debit передвигает observed и charged. При insufficient/capacity отказе
сохраняется blocked receipt, observed передвигается, charged остаётся прежним:
pending bytes не теряются. Повтор того же blocked sample возвращает старый отказ,
не списывает после пополнения. Новый валидный report покрывает весь pending delta,
включая ранее не списанные bytes. Частичного debit и отрицательного quota нет.
При retired epoch с долгом агент должен передать новый final-snapshot report с
новыми sequence/key и прежними counters/measurement time внутри этого epoch.
Новый epoch не переносит и не стирает долг старого. Следующий report с теми же
counters и большим sequence допустим и может закрыть pending после пополнения.

Binding/epoch creation запрещены для disabled owner/revoked device. Учёт уже
привязанного peer остаётся разрешён доверенному источнику после disable/revoke:
поздний расход не исчезает, новые sessions/peers от этого не выдаются. Narrow
usage-only ledger path допускает debit disabled account, обычный internal grant
такого права не получает. Реальное отключение peer/lease относится к I-012.

## Честные границы

Это accounting, не hard cutoff. При отсутствии/сбое агента или quota можно
наблюдать pending, но I-008 не ограничивает фактический расход на узле. Верхняя
граница перерасхода ещё не доказана: leases/reservations/provider enforcement
нужны до реального VPN. Нельзя объявить выполненными Q-04/Q-05 или SLO доставки
только по unit/DB tests. Локальный запуск использует synthetic reports, не VPN mock.

Sample receipts immutable (UPDATE/DELETE/TRUNCATE guards), epoch/stream — mutable
projections. Reconcile повторно вычисляет deltas/observed/charged/pending, проверяет
quota receipts и выводит только aggregate counts. Полный audit не исправляет rows.
Receipt включает отдельный arrival order и epoch_end_at_receipt: последующее
закрытие epoch не меняет причину прежнего отказа при повторной сверке.
Все receipts/epochs/bindings пока хранятся для жизни аккаунта; production retention,
compaction/dedup tombstones, deletion и backup policy нужны до реальных пользователей.
Только aggregate byte/time metadata; нет browsing logs или profile fields.

Migration 0005 и source=usage checks откатываются только с пустой usage schema и
без quota usage receipts; generic quota history/reconciliation понимают usage.
Новых dependencies нет. DB owner может отключить guards: это не защита от superuser.

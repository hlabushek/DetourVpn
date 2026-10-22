# ADR-014: ledger квоты и идемпотентные операции

Status: **Proposed — local implementation for review**, 2026-10-18 / I-007.
Scope: ledger, стартовое начисление, read API и сверка. Usage ingestion, rewards,
payments, node leases/enforcement и admin mutation API сюда не входят.

## Источник истины и операции

Количество — integer bytes, signed BIGINT; lifetime granted/used и available
ограничены 0..2^63-1. Денег/float в модели нет. Источник истины — append-only
quota_ledger; отдельного изменяемого balance/cache пока нет. Остаток вычисляется
как сумма credits минус сумма debits. Все writes берут один User FOR UPDATE,
что согласует их с auth/disable и сериализует операции одного аккаунта.
Debit применяется целиком либо сохраняется отказ insufficient_quota, без partial
debit и отрицательного balance. Перерасход от настоящего туннеля этим не ограничен:
его измерение и policy нужны в I-008/ADR-004 перед реальными узлами.

quota_operations хранит immutable receipt: user, UUID idempotency_key, kind,
amount_bytes, source_type/source_id, итог applied/rejected, error и balance snapshot.
Одинаковые key+payload возвращают исходный итог даже после других операций.
Изменённый payload с тем же key — quota_idempotency_conflict. Новый key для уже
использованного business source — quota_source_conflict. Отказы тоже коммитятся:
неуспешное списание нельзя неожиданно провести повтором после нового начисления.
На один accepted receipt ровно одна ledger row; rejected receipt не меняет ledger.
Запись receipt и ledger атомарна. SQL unique constraints защищают оба вида replay.

Ledger хранит последовательный номер внутри user, signed delta, kind, source,
idempotency key, balance_after и timestamp. Счётчики получаются SQL SUM(BIGINT),
PostgreSQL возвращает точный NUMERIC; Python использует int без float-конверсии.
Суммарный lifetime overflow/sequence overflow даёт сохранённый отказ, не wraparound.
UPDATE/DELETE/TRUNCATE journal/receipts запрещены DB triggers. Это защита от
ошибочных приложений, не от DB owner/superuser, способного отключить trigger.
Прямые INSERT вне сервиса не поддерживаются; runtime least-privilege role нужна
до staging. Reconcile выявляет нестыковки receipt/ledger, sequence и snapshots;
автоматической переписи/repair нет. Коррекция — новая credit/debit операция,
а не UPDATE старой записи; privileged workflow требует отдельного MFA/admin issue.

## Начальная квота

Размер в ТЗ не установлен; пример JSON не является тарифом. Настройка
VPN_INITIAL_QUOTA_BYTES: strict integer 0..2^63-1, по умолчанию **0**. Ненулевое
значение задаётся владельцем локально; тестовые суммы не являются product pricing.
При успешном code login внутри той же transaction создаётся registration receipt
и credit один раз/user. Canonical source_id и idempotency_key равны user_id.
Нулевой welcome credit — явная отметка обработанной регистрации: увеличение
настройки не начисляет снова старым аккаунтам. Повтор login/другое устройство
возвращает прежнее начисление. Старые accounts получают initial grant при первом
успешном code login после migration; GET и refresh ничего не начисляют.
Если welcome не может примениться из-за corrupted/full historical account,
login fail-closed откатывает auth transaction; изменение настроек не выполняет backfill.

## API и доверие

GET /api/v1/quota и /quota/history доступны только владельцу через quota:read.
Публичных credit/debit endpoints нет: внутренний LedgerService принимает команды
только от будущих доверенных workflow. Обычный пользователь и DB role admin без
MFA не получают mutation API. Source=internal — внутренний технический event UUID,
не утверждение о реальном трафике/платеже. Source=registration зарезервирован сервисом.
History использует immutable per-user sequence и snapshot_revision; новые записи
между страницами не меняют содержимое snapshot. Page size 1..100. Чужой user_id
не принимается параметром для выбора owner. API не хранит URLs/DNS/payload/мемо.

## Сверка, retention и ограничения

Ручной read-only reconcile проверяет все prefixes, terminal totals, receipts и
business/idempotency correspondence, не меняя данные. Проверка одной user history
держит User lock; reconciliation всех accounts идёт по одному и не является
глобальным snapshot. Расхождение — nonzero exit и безопасные агрегированные counts,
без Telegram IDs/ключей/токенов. Нужен review/restore/forward correction, не удаление rows.
Rollback migration запрещён при непустых quota tables; пустой downgrade удаляет
triggers/functions/tables. Indexes: user/sequence, idempotency, business source,
created_at. Immutable quota rows/receipts пока хранятся на время аккаунта: удаление,
анонимизация и backup retention должны быть определены до реальных пользователей.

SUM/reconcile работают за O(history), это корректностный local baseline, не
доказательство нагрузки production. При росте нужен audited materialized projection
и performance evidence; Redis не становится balance source. No new dependencies.

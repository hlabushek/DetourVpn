# ADR-008: реестр узлов и контракт node agent v1

Status: **Proposed — локальная реализация I-009 для review**, 2026-10-18.

## Решение

Узел получает случайный 256-bit bearer credential при локальном trusted provisioning.
База хранит только SHA-256 digest credential. Каждый HTTP запрос передаёт credential,
timestamp и UUID nonce в заголовках; timestamp принимается в окне ±60 секунд, nonce
уникален для узла и сохраняется в той же транзакции, что обработка запроса. TLS
обязателен на любом удалённом ingress; без TLS credential можно перехватить. Публичный
reverse proxy пока не настроен.

Текущий контракт `/api/internal/v1/agent/usage` принимает только `usage-sample.v1`.
Узел определяется по проверенной подписи, node_id из JSON не принимается. Расход
направляется в существующий I-008 engine. Идемпотентность бизнес-операции остаётся
на `(node_id, idempotency_key)` и `(counter_epoch, sequence)` в usage receipts.
Повтор HTTP-запроса с новым nonce может вернуть прежний receipt; повтор nonce
отклоняется. Неизвестная версия закрывается stable `agent_version_unsupported`.

Provisioning выдаёт raw secret только при первом использовании provisioning key;
повтор с теми же параметрами возвращает node id без секрета, а несовпадающие
параметры дают conflict. Credential rotation, disable/revoke administration,
peer provisioning, capabilities negotiation и command queue остаются отдельными
задачами. Административные endpoints закрыты до RBAC/MFA.

Запрещены произвольные shell-команды и пользовательские поля URL, DNS, payload или
private keys. Counters — целые bytes. Никакой реальный node agent, VPS или tunnel
этой реализацией не создаётся. Credential rotation потребует отдельной версии/ADR;
nonce retention и production secret delivery policy нужны до staging.

## Риски и ограничения

- Утечка узлового секрета позволяет аутентифицировать запросы до его отзыва; сейчас
  lifecycle управления узлом доступен только доверенному коду, не публичному API.
- SHA-256 digest подходит для случайного высокоэнтропийного ключа, но не для паролей.
- Nonce таблица растёт без retention job; production TTL/cleanup и мониторинг нужны
  до подключения узлов.
- Per-node rate limit пока отсутствует; до удалённого подключения нужен ingress rate limit.
- Timestamp требует синхронизации часов и HTTPS. Bearer credential rotation не реализован.
- Локальные PostgreSQL тесты подтверждают контрактные свойства, не сетевую установку.

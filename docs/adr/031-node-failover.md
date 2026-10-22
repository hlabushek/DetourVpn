# ADR-031 — I-019: bounded multi-node selection and expired-lease recovery

Accepted for local implementation, 2026-10-22.

Операторский private profile поддерживает прежний одиночный объект либо
version=1 manifest с 1–2 profiles. Endpoint задаёт только оператор: числовой
глобальный IPv4 и UDP port; DNS/URL/private endpoints не допускаются. Это
разрешение конфигурации, не разрешение покупать/настраивать VPS. Старый профиль
сохраняет прежний endpoint по умолчанию. Один peer/узел и lease 60s сохранены.

Каталог доступен только с tunnels:manage: id/name/region и reason, без endpoint,
ключей и runtime. Availability означает active registry, negotiated v2/runtime,
heartbeat не старше 10s и свободный slot. Это не доказательство egress. POST
принимает optional node_id; без него выбирает первый доступный profile. Выбор
повторно проверяется при admission под slot lock. Explicit unavailable node
не заменяется молча. Idempotency учитывает выбранный explicit node_id.

Переключение ручное: остановить local VPN, отозвать старый lease, выбрать B,
создать новый intent. Пока старый peer не завершён — новый на этом устройстве
запрещён. API outage сама по себе не завершает живой local lease.

Если A недоступен, owner может явно POST /tunnels/{id}/recover только после
lease_deadline + 10s и stale heartbeat. Сохраняется правило I-012 counters_lost:
весь reservation списывается, observed_bytes=NULL. Это консервативное списание,
не измеренный usage; оно не квалифицирует referral. User/Tunnel locks и ledger
key=tunnel UUID сериализуют recover/retry/ACK. Peer version увеличивается,
запоздалые команды не становятся действительными. ACK не подделывается:
старые команды остаются unacknowledged, но больше не текущими. При возвращении
агент удаляет неизвестный/истёкший peer по существующей reconciliation policy.
Ни клиент, ни сервер не считают stale heartbeat доказательством остановки peer:
безопасность опирается на уже обязательный kernel lease expiry I-012.
Staging assumption: подтверждённая погрешность часов узла/API не более 1s и
native gate operation не дольше существующего subprocess timeout 5s. Запас
10s покрывает обе величины. Неограниченный clock jump/враждебный агент этим
протоколом не решаются; без проверки этих условий live acceptance запрещена.

Новых таблиц/зависимостей нет. Existing settled lease повторно не списывается.
Автоматический бесшовный failover, масштабирование capacity и новые транспорты
не входят. Потеря счётчиков может стоить пользователю полного бюджета; UI
показывает это до recovery. Public commercial usage требует другой settlement
policy и отдельного решения.

Приёмка разделена: real local API/PostgreSQL + TEST ONLY nodes; Flutter и
Android build/unit checks; реальный drill с двумя разрешёнными staging узлами,
physical client, egress/cutoff/expiry/recovery/cleanup — отдельный открытый gate.

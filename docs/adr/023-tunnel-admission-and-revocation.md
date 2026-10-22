# ADR-023: durable tunnel requests и fail-closed admission

Status: Accepted for local implementation, 2026-10-19. I-012 остаётся открытым.

Историческая первая поставка. Admission и daemon расширены ADR-024;
описанное здесь закрытое admission остаётся default без trusted node profile.

## Решение и границы

I-010/011 приняты в границах ADR-022. Они не утвердили proof владения ключом,
quota reservation/lease и node daemon. Эти security-critical решения нельзя
заменять произвольным TTL или флагом `allow_unsafe`.

Первый исполняемый пакет I-012 сохраняет owner-only запрос с UUID idempotency
key, возвращает HTTP 202 и `admission_pending`, без конфигурации. Это ожидание
проверки условий выдачи, а не команда установки peer. Автоматического перехода
из этого состояния нет. Клиент не должен считать 202 подключением. API принимает
только device_id и key; session должна быть привязана к тому же активному device.
Не создаются peer, usage stream, reservation или списание квоты.

Повтор `(user_id, idempotency_key)` возвращает тот же tunnel и его текущий статус;
другой device даёт conflict. Для одного device допускается один незавершённый
запрос. Чужие и неизвестные ID возвращают одинаковый 404. Читать/отзывать свой
запрос можно с другой действующей сессии владельца, в том числе после device revoke.

Отзыв запроса без назначенного узла завершается сразу: peer ему никогда не
выдавался. Если node_id уже назначен доверенной будущей orchestration, отзыв
атомарно создаёт `remove_peer` в durable outbox и возвращает `revoke_pending`.
Device revoke сохраняет те же команды в своей транзакции. 204 devices означает
отзыв control-plane устройства/сессий; состояние туннеля читается отдельно.
Схема не предоставляет способа назначить узел через HTTP или включить admission.

## Protocol peer-removal.v1

Authenticated POST poll отдаёт до 100 незавершённых команд только своему node.
Повторы безопасны, команда не удаляется после доставки. Ack требует точные
command_id, peer_id, peer_version и outcome `removed`. Идентификатор peer равен
идентификатору tunnel; публичный ключ берётся из owner-bound Device.
Capabilities и contract_version проверяются до обработки. В body нет node_id,
shell/path/URL/private key. Node определяется I-009 credential + nonce/timestamp;
nonce и изменения outbox фиксируются одной транзакцией. Ошибки откатывают nonce.

Ack подтверждает заявление доверенного узла, а не наблюдение контрольной
плоскости. Агент обязан удалить peer через native UAPI, проверить отсутствие и
только затем отправить ack; отсутствие daemon и live проверки явно остаётся gate.
Дубликат точного ack возвращает `revoked`, чужая/неизвестная команда — 404,
устаревшая версия — 409. Reconciliation после потери связи — повтор poll/удаления;
полная сверка inventory и add_peer относятся к оставшейся части I-012.

## Транзакции, данные и риски

Порядок блокировок: public auth User → SessionFamily → Device/Tunnel → outbox;
agent authenticate VPNNode → User → Tunnel → outbox. Public операции не берут
VPNNode lock. Один User lock сериализует create/delete/device revoke/ack.
Poll читает outbox без User lock, поэтому может вернуть уже применённую команду;
повтор удаления обязателен и безопасен. Downgrade с данными запрещён.

Новых секретов/зависимостей нет. Сохраняются user/device/node UUID, публичный ключ
устройства в существующей таблице, request UUID, состояния/версии и timestamps.
История URL/DNS/payload не собирается. Receipts/outbox сохраняются для replay и
восстановления, автоматического удаления нет; production retention и deletion
policy требуют отдельного утверждения до сбора данных реальных пользователей.
Нет гарантии отсечения трафика при offline node; без lease admission остаётся
закрытым. Нет worker, назначения node или рабочего пользовательского config.

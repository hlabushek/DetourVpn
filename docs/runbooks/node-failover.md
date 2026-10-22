# I-019 — выбор узла и bounded recovery

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
independent provider и SSH transport API. Production root credentials не нужны.

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

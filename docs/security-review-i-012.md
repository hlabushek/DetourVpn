# I-012: отдельный локальный security review

2026-10-19. Выполнен исполнителем как отдельная проверка изменённых trust boundaries.
**Не независимый аудит и не основание для публичного выпуска.** Scope — ADR-024.

Дополнение 2026-10-20: физическая приёмка завершена — выдача/отзыв через API,
настоящий restart агента и kernel expiry при потере канала управления прошли
на VPS/Pixel. Во всех сценариях подтверждены handshake, staging egress,
прекращение трафика, однократный settlement и восстановление обычной сети.
Cleanup восстановил семь baseline checks; удаление grant и временных credentials,
остановка служб и отсутствие forwarding проверены дополнительно.
Результат: [`i-012-staging-lifecycle.json`](evidence/i-012-staging-lifecycle.json).
Это закрывает bounded staging gate; перечисленные ниже production release gates
и необходимость независимого review сохраняются.

## Проверенные границы

- Client private key не входит в API. Key confirmation использует PyCA X25519
  и platform HMAC; challenge привязан к owner/device/session, TTL и consumed flag.
  Low-order public keys fail closed. Android использует pinned upstream Curve25519;
  real Python/Android interop подтверждён финальным live тестом.
- Owner определяется auth principal, FK сохраняют ownership. Public API не
  принимает node address, shell, file path, private key, observed counters или ACK.
  Config доступен только нужной bound session после assigned node ACK и до expiry.
- User lock объединяет proof consumption, reservation и outbox. Node capacity
  сериализуется отдельным advisory lock + unique index; public path не берёт
  VPNNode row lock, поэтому не инвертирует agent Node→User lock order.
- Quota reservations вычитаются из available для любых debit, snapshots сохраняют
  reserved_before для проверки durable rejection. Settlement release/debit/state
  атомарны; replay не списывает повторно. Budget потерянных counters сохраняется
  консервативно, rollback не освобождает квоту только по wall-clock expiry.
  Lease source `tunnel_lease` сверяется с reservation; I-008 bind такого peer
  запрещён в коде. Общая usage/ledger audit проверена после settlement и при hold.
- Desired peer version и exact command/node checks не дают stale add ACK оживить
  отозванное устройство. V1 removal не может завершать v2 reservation без settlement.
- Native daemon имеет только fixed AWG/nft operations. Interface/namespace/IP/port,
  transport target и binary hash фиксированы. Protected journal предшествует add;
  повтор не обновляет gate, startup удаляет peer и сохраняет counters/tombstone.
  Crash между add и journal commit помечает counters lost, не возвращает бесплатно budget.
- Kernel gate настроен до add, общий для inbound/outbound endpoint UDP. Timeout
  независимо заканчивается после одного применения, agent/API loss его не обновляет.
  Gate/peer removal реально проверены локальными pinned AWG binaries и Linux kernel.
- Между systemd units сеть выбирается только по pinned namespace fd проверенного
  root AWG core: ready metadata, exact cgroup, executable hash и namespace inode.
  Host namespace, неверный owner/binary/cgroup/inode и bool вместо PID отклоняются.
  Descriptor не наследуется случайно; он явно передаётся только fixed nft child.
  Реальная граница private mount namespaces воспроизведена отдельным Linux test,
  включая выполнение из второй sandboxed unit. Capabilities не расширены.
- Root controller изолирован от caller imports, проверяет host/UID/boot/clocks и
  script/unit hashes. Input ограничен, actions allowlisted, arbitrary path/shell нет.
  Code change инвалидирует grant. Ручной expire отменяет timer, чтобы поздний job
  не затронул следующую разрешённую сессию. Grant удаляется даже при stop/timer failure;
  это покрыто отдельными отрицательными тестами.
- Credentials/profile не попадают в публичные schemas, ошибки или pytest/stdout.
  Windows runtime secrets — user-only ACL вне OneDrive, staging secrets — root-only
  `/run`. Secret profile stdout перехватывается только private каналом. Runtime
  environment/командные строки не содержат паролей; sudo вводит владелец локально.
- Диагностика daemon допускает только фиксированный error и startup stage;
  root parser отбрасывает произвольный journal. Android test сохраняет HTTP status
  и allowlisted API code без response body. Live harness ждёт настоящую v2
  negotiation в PostgreSQL до телефона и выдаёт passed receipt только после
  завершения всех сценариев и подтверждения control-plane loss/recovery.

## Ограничения, которые нельзя скрывать

- Это новый key-confirmation workflow на стандартных primitives; внешний review
  composition, deployment и секретов нужен перед реальными пользователями.
- Один peer на выделенный endpoint, короткий lease, renewal отсутствует. Noise UDP
  тоже расходует kernel budget: DoS может завершить lease раньше. Не обещать DDoS protection.
- Native tx включает попытки отправки, позже отброшенные gate. Сохраняется observed,
  charge capped reservation. Lost counters → full budget. Это тестовая политика,
  требующая отдельного продуктового/эксплуатационного решения до оплаты/production.
- Secret profile вручную доверяется оператором, remote ingress/credential rotation,
  retention/deletion/backup и production secret manager не выполнены этой issue.
- API `active` означает подтверждённое применение peer, не measured connection.
  Отсутствие всех DNS/IPv6 leaks не доказано. Старые измерения I-011 ограничены;
  новый live test измеряет конкретный egress/revoke/restart/offline путь.
- Локальные systemd syntax checks не являются запуском на VPS. Новый privileged
  grant и systemd sandbox проверяются фактическим финальным запуском владельца.
  Independent security review, полный SBOM/licence release gate остаются открыты.

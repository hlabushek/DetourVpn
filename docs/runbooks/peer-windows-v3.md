# I-024 — воспроизведение и эксплуатационный контракт v3

Локальная реализация backend/agent/Linux. Развёртывание на постоянных узлах и
физическая приёмка — I-028; Android renewal — I-025. Этот документ не разрешает
remote deployment, изменения firewall или переключение существующего VPN.

## Состав и поток событий

`window_routes.py` проверяет пользовательскую сессию/устройство или node credential,
timestamp и одноразовый nonce. `window_service.py` сохраняет admission/renew/revoke
как транзакционное намерение. `window_funding.py` резервирует конкретные funding
lots. В транзакции нет сетевых запросов и native commands.

Агент опрашивает hidden `/api/internal/v3/windows/{inventory,start,poll}` через
HTTPS. `window_runtime.py` фиксирует intent в private SQLite до открытия gate.
`window_peer.py` работает с одним фиксированным AWG peer в операторском slot,
`window_gate.py` ограничивает обе стороны общего UDP budget и kernel deadline.
Rotation закрывает gate, сохраняет receipt, переустанавливает budget и открывает
следующее окно с теми же ключом и адресом. Receipt повторяется до точного ACK.
Backend один раз списывает измеренные байты из исходных зарезервированных lots.

Unknown — подтверждённое закрытие без достоверного финального счётчика. Оно не
создаёт usage debit и не освобождает bounded hold. Повторный receipt не может
заменить unknown на measured. После восстановления slot можно использовать для
нового соединения, но удержанная квота требует отдельной аудируемой компенсации/
операторской сверки. Автоматическое угадывание расхода запрещено.

## Локальные проверки с Windows

Из корня репозитория, PowerShell:

```powershell
uv run python -m ruff format --check .
uv run python -m ruff check .
uv run python -m mypy
uv run python -m mypy --platform linux services/node-agent/window_daemon.py services/node-agent/window_gate.py services/node-agent/window_peer.py scripts/check_v3_awg_runtime.py scripts/check_v3_awg_slots.py scripts/check_v3_kernel_gate.py
uv run python scripts/export_openapi.py --check
uv run python scripts/export_usage_contracts.py --check
uv run python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
```

PostgreSQL helper создаёт случайный disposable cluster, слушает только loopback,
использует synthetic credentials, останавливает сервер и удаляет только свой
cluster. Для сокращённого набора можно установить `PYTEST_ADDOPTS` со списком
`test_peer_windows.py`, `test_window_funding.py`, `test_window_service.py`,
`test_window_runtime.py`, `test_window_daemon.py` в `services/api/tests/`;
после запуска убрать переменную. Сервисные тесты используют настоящий PostgreSQL,
но fake Telegram delivery и simulated node/kernel — это не VPN acceptance.

## Изолированные Linux/AWG проверки

Нужен локальный WSL Linux с `ip`, `nft`, `nsenter`, `unshare`, `ping`, `mount`,
Python 3.13, root внутри WSL и заранее проверенными pinned артефактами
`.cache/i-010/artifacts/{awg,amneziawg-go}`. Скрипт ещё раз проверяет SHA-256.
Root внутри локального WSL не означает использование root SSH credentials.
Пути с пробелами должны передаваться одним аргументом.

В Linux, из корня checkout (`$PWD` — абсолютный Linux путь к нему):

```bash
python3 scripts/check_v3_kernel_gate.py
python3.13 -m venv /root/.cache/detourvpn-i024-venv
/root/.cache/detourvpn-i024-venv/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple --require-hashes --only-binary=:all: \
  -r services/node-agent/window-requirements.txt
env PYTHONPATH="$PWD/services/api/src" unshare --mount --net -- \
  /root/.cache/detourvpn-i024-venv/bin/python scripts/check_v3_awg_slots.py --runtime
```

Последняя команда занимает несколько минут. Внешний mount/network namespace
обязателен: `/run` заменяется private tmpfs только внутри него. Временные ключи
создаются там с mode 0600, не печатаются, не сохраняются в evidence. Все четыре
native core закрываются в `finally`; namespaces исчезают с изолированным процессом.
Нет host firewall mutation, WAN egress, использования существующих узлов или Android.
`check_v3_kernel_gate.py` отдельно проверяет конкурентную точную границу UDP budget,
duplex meter, независимый timeout и private journal lock/link/permission checks.

## Обязательная ручная подготовка перед I-028

Сначала read-only baseline, конкретный план, rollback и разрешение на dataplane.
Нельзя направлять готовый daemon на произвольный production host.

1. Подготовить root-owned код/venv и pinned AWG артефакты. Node package включает
   `window_daemon.py`, `window_gate.py`, `window_peer.py` и только pure модули
   `vpn_api/tunnels/{keys,v3_contracts,window_journal,window_runtime}.py` с package
   `__init__.py`. Backend settings/секреты и SQLAlchemy/FastAPI на узле не нужны.
   Зависимости CPython 3.13/Linux x86_64 закреплены в `window-requirements.txt`.
2. Операторский bootstrap должен подготовить по одному core/netns/interface
   `dv3s1`…`dv3sN` и фиксированные UDP порты `51901`…`51900+N`, отдельные server
   keys/header-protection keys, адрес `10.203.71.1/30`, разрешённый peer
   `10.203.71.2/32`, маршрутизацию и запрет переходов между slots. Каждый gate
   изначально закрыт, до exposure UDP. Политика host forwarding/NAT и egress
   проверяется отдельно; daemon её не создаёт. N ≤16 — предел протокола,
   фактическая ёмкость маленького VPS ещё не измерена.
3. Root-private `/etc/detourvpn-node/agent.json` содержит только `node_id`,
   `api_origin` (HTTPS origin на 443, без credentials/path/query/fragment) и
   `slot_count`. `/etc/detourvpn-node/credential` — отдельный node-scoped secret;
   все secret files 0600, private directories 0700, без symlink/hardlink.
   Trusted HTTPS node route должен быть доступен только по согласованной политике:
   существующий публичный Caddy намеренно блокирует `/api/internal/*`.
4. Root-private `/run/detourvpn-node/slots/{slot}.json` должен содержать
   `slot_id`, `native_pid`, `native_netns_inode`, `server_public_key`. PID/namespace,
   исполняемый core, AWG hash, public key и listen port проверяются агентом.
   AWG располагается строго в `/opt/detourvpn-node/current/awg`; пути/команды
   никогда не передаются через control API. Нужен supervision native cores и
   daemon с корректной последовательностью startup/stop; installer этих служб
   в рамках локальной приёмки не выполнялся.
5. `/var/lib/detourvpn-node` — root-private persistent storage. Агент открывает
   `state.sqlite` с WAL/FULL и process-lifetime flock. Не удалять журнал/WAL/lock
   ради «починки», не запускать два агента на одном state, не использовать
   Windows ACL как проверку Linux production permissions.
6. В API подготовить private `VPN_WINDOW_MANIFEST_FILE`: JSON `version=3`,
   `nodes` с `node_id` и contiguous `slots`, где заданы `slot_id`, public
   `endpoint`, `server_public_key`, `header_protection_key`. Inventory не должен
   пересекаться с v2. Header-protection key — секрет, даже если конфигурация
   передаётся авторизованному клиенту; не печатать manifest/config в логи.
   Для runtime API допустим root-owned manifest 0640 с отдельной группой API
   и root-owned каталогом 0750: runtime читает его, но не может менять.
   Обычный пользователь получает конфигурацию без client private key только
   после active ACK. Включение требует auth и явного
   `VPN_ENABLE_WINDOW_TUNNELS=true`; по умолчанию выключено.
7. Перед миграциями сохранить зашифрованную копию и проверить restore отдельно.
   Выполнить штатный migration wrapper ролью migrator до
   `0015_window_admission`, проверить readiness. Runtime DB role без DDL.
   Миграция 0015 backfill из immutable JSON проходит в одной DDL transaction.
   Откат ниже 0015 запрещён при истории runtime/admission, ниже 0014 — при
   window history. После использования нужен forward fix либо отдельно
   согласованное восстановление с fencing, а не удаление истории/таблиц.

## Сбой, отзыв и восстановление

При недоступном API kernel budget/deadline продолжают работать. Агент не создаёт
новые окна offline. Grant длится максимум 90s/64MiB; native opening вычитает 6s
для operation/clock margin. Для долгого соединения клиент должен заранее
резервировать/запрашивать следующее окно. Слишком поздний retry его не оживляет.
Отзыв HTTP 202 означает намерение; закрытие подтверждает только receipt.

Restart закрывает все gates до reconciliation, меняет runtime UUID, фиксирует
старые measured/unknown receipts и снимает native peers. CAS epoch history не
позволяет старому runtime зарегистрироваться снова. Точное ACK-loss replay не
сбрасывает budget/deadline. Запоздалый revoke старой генерации закрывает также
текущую генерацию того же connection, но не другое соединение в переиспользованном slot.

После восстановления старой БД **до reconnection** обязательно отозвать/сменить
agent credentials и пользовательские sessions; старая CAS history сама по себе
не защищает от всех сообщений, отправленных после snapshot. Подробный drill,
off-host backup scheduler и мониторинг — I-027/I-028.

Сохраняются только operational IDs, public keys, состояния и aggregate counters;
не DNS/URLs/payload/history browsing. Ledger/receipt history immutable, её удаление
и production retention policy не вводятся этим issue. Нужны отдельные legal/
retention решения перед production. Outer-IP meter включает headers, handshake
и принятый UDP на endpoint до native authentication; это следует раскрыть
пользователю и поддерживать аудируемую компенсацию. Root/node compromise,
независимый provider outage, DDoS, DNS/IPv6 и physical daily use не приняты здесь.

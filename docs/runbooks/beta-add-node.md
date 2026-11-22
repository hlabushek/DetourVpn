# Добавление нового beta-узла (ADR-094, этапы 1–3)

## Быстрый путь (две команды)

```powershell
uv run --frozen python -m scripts.add_beta_node prepare --name france --host <IPv4> --country FR --city Paris
uv run --frozen python -m scripts.add_beta_node run --node detourvpn-beta-france
```

По умолчанию `run` ставит узел в режиме пула (ADR-101) на 32 одновременных устройства
(`--pool-peers 32`; `--pool-peers 0` — прежний режим «одно устройство на слот») и включает
UDP-эхо для пинга на порту 51899 (ADR-102): откройте этот порт у провайдера.

`prepare` добавляет узел в инвентарь, создаёт SSH-ключ и печатает одну строку, которую нужно
вставить на новом сервере (Ubuntu 24.04). `run` выполняет предпроверку, установку, регистрацию,
привязку и включение; завершённые этапы пропускаются, поэтому после сбоя команду просто
запускают снова. После этого обязательна приёмка с телефона (раздел 5). Подробные шаги ниже
нужны для диагностики.

Каждая команда по умолчанию только читает (сухой прогон); изменения делает `--execute`.
Новый узел должен пройти все шаги по порядку. Ни один шаг не доказывает handshake и egress:
это отдельная физическая приёмка (ADR-061/062).

## 1. Подготовка хоста (вручную)

1. Новый VPS: **Ubuntu 24.04**, x86_64, публичный IPv4 назначен интерфейсу напрямую, один маршрут
   по умолчанию, `net.ipv4.ip_forward=1` (ADR-052). Ubuntu 22.04 и Debian 12 не подходят:
   код узла рассчитан на `systemd-executor` из systemd 255.
2. Добавьте на хост публичную часть ключа `~/.ssh/detourvpn-beta-*`, сверьте отпечаток хоста с
   консолью провайдера, один раз подключитесь вручную (хост попадёт в `known_hosts`). Если сервер
   переустанавливали, сначала удалите старую запись: `ssh-keygen -R <адрес>`.
3. В панели провайдера (если есть фаервол) откройте UDP-порты слотов 51901–51916.
4. Фаервол на самом хосте (ADR-172). Если включён `ufw` с политикой `deny (routed)` (проверка:
   `sudo ufw status verbose`, строка `Default:`), он отбрасывает пересылаемый трафик слотов,
   даже когда правила проекта его разрешают: accept в одной цепочке nftables не отменяет drop
   в другой цепочке того же hook. Bootstrap такой хост не запускает
   (`host_forward_policy_drops`). Добавьте на НОВОМ хосте по паре правил на каждый слот N
   (узел-пул: только N=1), где `<UPLINK>` — интерфейс маршрута по умолчанию
   (`ip -4 route get 1.1.1.1`, поле `dev`):

   ```bash
   sudo ufw route allow in on dvbh<N> out on <UPLINK>
   sudo ufw route allow in on <UPLINK> out on dvbh<N>
   ```

   Политику `DEFAULT_FORWARD_POLICY` на `ACCEPT` не меняйте: хост начнёт пересылать чужой
   трафик. Проверка видит только правила iptables-nft/nftables (стандарт Ubuntu 24.04); при
   iptables-legacy её результат недостоверен. Docker с политикой FORWARD `DROP` даёт тот же
   отказ.

## 2. Инвентарь и предпроверка

1. Скопируйте `infra/beta/nodes.example.json` в `infra/beta/nodes.json` (файл в `.gitignore`) и
   добавьте запись узла (`udp_ports`: `[51901, 51916]`).
2. Предпроверка (только чтение):

```powershell
uv run --frozen python -m scripts.check_candidate_node --node detourvpn-beta-<город>
```

Нужен вердикт `ready` (выход 0).

## 3. Установка узла на новый хост

```powershell
uv run --frozen python -m scripts.install_beta_node --node detourvpn-beta-<город>
uv run --frozen python -m scripts.install_beta_node --node detourvpn-beta-<город> --execute
```

Первая команда сухой прогон. Вторая ставит узел и запускает закрытый слот; агент не запускается.
Ошибка откатывает всё созданное этим запуском.

## 4. Регистрация, привязка, включение

```powershell
uv run --frozen python -m scripts.connect_beta_node status --node detourvpn-beta-<город>
uv run --frozen python -m scripts.connect_beta_node register --node detourvpn-beta-<город> --execute
uv run --frozen python -m scripts.connect_beta_node bind --node detourvpn-beta-<город> --execute
uv run --frozen python -m scripts.connect_beta_node activate --node detourvpn-beta-<город> --execute
```

Без `--execute` каждая команда только проверяет. Откат включения:
`connect_beta_node rollback --node ... --execute` (останавливает агент, строка снова `disabled`).
Перезапуск API не нужен: он читает манифест на каждый запрос.

## 5. Приёмка (вручную, до приглашения кого-либо)

Физическое подключение с телефона к новому узлу: handshake, внешний IP из сети нового узла,
учёт трафика. Только после этого узел считается принятым.

## Журнал

- 2026-11-09: кандидат `detourvpn-beta-germany` (VPS xorek). Ubuntu 22.04: установка упала на
  запуске bootstrap (`slot_core_identity_conflict`, systemd 249), откат чистый. Нужна переустановка
  с Ubuntu 24.04. Старый receipt предпроверки (22.04 `ready`) устарел, актуальный:
  `docs/evidence/beta-node-germany-preflight-20261109-corrected.json`.
- 2026-11-09 (позже): VPS переустановлен на Ubuntu 24.04; предпроверка `ready`, установка, регистрация, привязка (Caddy и манифест, 2 узла) и включение прошли; агент активен и опрашивает API, автозапуск включён. Первая привязка откатилась из-за неверной пробы Caddy (исправлено). Приёмка с телефона не выполнена.
- 2026-11-09 (позже): владелец сообщил, что подключение к Германии с телефона прошло и внешний IP немецкий. Это отчёт владельца, независимой проверки и проверки учёта трафика нет.
- 2026-11-09 (позже): владелец включил автозапуск `detourvpn-beta-node-agent.service` на узле Хельсинки (`systemctl enable`, результат `enabled`); раньше после перезагрузки узел не поднялся бы.
- 2026-11-09 (позже): третий узел `detourvpn-beta-netherlands` (NL, Dronten) добавлен мастером `add_beta_node run` с первого раза: preflight, установка, регистрация, привязка и включение прошли. Приёмка с телефона не выполнена.
- 2026-11-09 (позже): владелец сообщил, что подключение к узлу в Нидерландах работает. Отчёт владельца; независимой проверки внешнего IP и учёта трафика нет.

# ADR-024: bounded staging admission I-012

Status: accepted for bounded staging, 2026-10-20. All three actual VPS/Pixel
scenarios and cleanup passed: `docs/evidence/i-012-staging-lifecycle.json`.

Scope: один активный peer на один выделенный staging endpoint; существующие VPN
не меняются. Это ограниченный sandbox, не commercial launch или multi-node fleet.

## Proof

До назначения peer клиент доказывает владение зарегистрированным X25519 key.
Сервер создаёт ephemeral X25519 ключ через PyCA cryptography и 32-byte nonce.
Ответ — HMAC-SHA256(shared secret, ASCII domain + challenge UUID + nonce).
Сохраняются только SHA256 ожидаемого ответа, owner/device/session UUID и expiry
120s. Ephemeral private key/shared secret не сохраняются; клиентский private key
никогда не отправляется. Proof привязан к одной session family, одноразовый,
не переносится на другое устройство/запрос. X25519 low-order inputs fail closed.
HMAC — key confirmation, не подпись и не универсальный signing API.
Используются upstream X25519 и стандартный HMAC, собственных primitives нет.

## Lease и quota

Выделенный UDP endpoint обслуживает ровно один peer. Лимит на уровне ядра считает
весь внешний IPv4 UDP трафик этого endpoint в обоих направлениях, включая headers
и AWG junk. Это ограничение допуска внешних UDP packets, не замена I-008
счётчиков. Одна общая nft quota и timeout gate применяются до добавления peer.
Максимальный срок lease 60 секунд, budget от 64 KiB до 16 MiB; default 1 MiB.
Пересекающие budget packets отбрасываются; timeout не зависит от agent/API.
Автоматического renewal нет, повтор команды никогда не продлевает deadline.
Native tx может включать уже зашифрованный пакет, который затем отбросил kernel
gate. Поэтому native rx+tx не объявляется hard-capped счётчиком: при settlement
сохраняется observed total, списание ограничивается зарезервированным budget.
Это явная sandbox политика, не потеря отрицательной delta или обещание точного
соответствия billed bytes пакетам на внешнем проводе. Нельзя выдавать lease этого
peer также в независимый I-008 pipeline и списывать повторно.

Бюджет резервируется атомарно под User lock; concurrent debit не может потратить
reserved bytes. После подтверждённого удаления peer reservation закрывается и
native rx+tx списываются один раз. При reboot/утрате counters расход консервативно
равен всему reserved budget; возврат не происходит по одному wall-clock expiry.
Если агент перезапущен при сохранённом runtime, он закрывает gate, снимает
финальные counters и удаляет peers; потерянные/неоднозначные counters отмечает
явно. Новый runtime UUID инвалидирует старые назначения и приводит к remove,
а не восстановлению add. User lock сериализует admission/revoke/settlement;
VPNNode lock агентского auth никогда не берётся из public request.
Это sandbox policy до real-user pricing, не оплаченный тариф. I-008 binding не
создаётся для этих lease, чтобы не было второго независимого списания.
Settlement использует отдельный ledger source `tunnel_lease`, source/key равны
tunnel UUID. Ledger reconciliation сверяет такие receipts с settled reservation;
I-008 audit не принимает их за usage samples. Попытка I-008 bind managed peer
отклоняется `usage_managed_by_lease`, а не только запрещается инструкцией.

## Readiness и секреты

Node profile — явно доверенный локальный secret file с public server key,
endpoint, AWG parameters и HeaderProtectionKey; server private key остаётся узлу.
Secret profile не пишется в DB/logs/OpenAPI examples. Временные файлы Windows
находятся в LocalAppData/ProjectVpn/i012-private вне OneDrive checkout.
Его выдача допускается только
owner-bound session, после applied ACK правильного node/command/version и до
expiry. Профиль на диске требует private permissions/secret-manager на deployment.
API restart не означает ready без сохранённого ACK. Node startup обязан удалить
оставшиеся peers и сверить desired/observed inventory; restore старого add запрещён.

При device revoke сохраняется remove с новой peer version; stale add ACK не
делает tunnel active. Lease reservation сохраняется до удаления/settlement.
Owner logout сам по себе не является peer removal; device/tunnel revoke является.

Sources: https://cryptography.io/en/latest/hazmat/primitives/asymmetric/x25519/
и https://netfilter.org/projects/nftables/manpage.html.

## Staging privilege и transport

Существующий I-011 grant отозван. Новый root-owned `i012_control.py` получает
отдельное 45m разрешение только после локального sudo prompt. UID/host/boot,
wall+monotonic deadlines и hashes всех пяти scripts/двух units проверяются.
Root helper принимает только prepare/start/profile/status/stop/restart-agent/expire;
никаких произвольных путей, shell, адресов или renewal. Input prepare ограничен
4096 bytes и строгими полями. Profile — отдельный secret stdout channel,
перехватывается напрямую в user-only ACL file и никогда не показывается в чате.

Agent обращается только к loopback 18765, который защищён SSH reverse forwarding
к loopback API на компьютере владельца. Android test APK использует loopback
18766 через авторизованный ADB reverse. Публичного plaintext HTTP ingress нет.
Секрет SSH остаётся локально: временная ACL-protected Windows copy нужна из-за
несовместимых UNC permissions; она удаляется после runner. Исходный WSL key
не меняется. Credentials и profiles удаляются из runtime при cleanup, synthetic
test DB уничтожается, test APK удаляется, app останавливается. Ни production
deploy, ни покупок/изменений existing service containers нет.

Endpoint и agent ограничены 600s каждый; cleanup проверяет семь baseline hashes
из актуального systemd InvocationID. Root timer вызывает expire после 45m даже
при потере SSH. Live результат подтверждён 2026-10-20 всеми тремя сценариями
в `docs/evidence/i-012-staging-lifecycle.json`; cleanup и удаление grant проверены.

## Namespace между двумя systemd службами

Фактическая попытка staging дошла до телефона, но agent завершился при startup.
Отдельный LOCAL WSL/systemd тест воспроизвёл границу: netns bind mount создан
в private mount namespace endpoint unit, внешний `/run/netns/vpn-i011` остаётся
placeholder. Поэтому agent не должен выбирать сеть через внешний `ip netns exec`.
Такое поведение соответствует [systemd.exec](https://www.freedesktop.org/software/systemd/man/256/systemd.exec.html):
изоляция mount namespace не передаёт новые mount points обратно на host.

Решение: endpoint добавляет native PID и netns inode в root-owned `ready.json`.
Agent открывает `/proc/PID/ns/net` и сохраняет fd на весь свой срок жизни.
До использования проверяет root ownership, точный endpoint cgroup, SHA-256
исполняемого pinned AWG core, совпадение namespace inode до/после проверки и
отличие от namespace самого агента (host). Неверный/stale PID, чужая служба,
подменённый бинарник или host namespace приводят к отказу до nft/peer add.
Все идентификаторы приходят из локального root-owned файла, не из API.

Только фиксированные nft операции выполняются через
`nsenter --net=/proc/self/fd/FD -- /usr/sbin/nft ...` с явно переданным fd.
Нет PID lookup на каждом вызове, общего shell, входа в mount/user namespace
или дополнительных capabilities. HTTP agent остаётся в host loopback namespace.
FD закрывается при остановке процесса; повторный startup заново проверяет
идентичность endpoint. Unit выводит только allowlisted error/stage labels;
root status повторно фильтрует journal по актуальному InvocationID.

LOCAL WSL test `scripts/i012_systemd_acceptance.py` сначала подтверждает отказ
старого пути, затем настоящий namespace pin, nft timeout и отдельную agent unit
с теми же restrictions. Это проверка границы Linux/systemd, не WAN acceptance.

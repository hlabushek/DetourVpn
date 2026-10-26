# ADR-049 — стабильные ключи слотов beta node

Статус: принято для локального offline config compiler I-030; реальные beta
ключи не создавались, файлы на host/API не устанавливались.

## Контекст

Временный `infra/staging-v3/runtime.py` генерирует server private key и
`HeaderProtectionKey` внутри `/run` при подготовке временного окружения. После
перезапуска эти ключи меняются, тогда как API `WindowManifest` должен содержать
соответствующие server public key и `HeaderProtectionKey`. Переносить staging
runtime в постоянный node нельзя.

## Решение

`services/node-agent/beta_slot_config.py` принимает версионированный operator
source с node UUID, HTTPS API origin, непрерывными slots, public IPv4 endpoint,
server private key и header-protection key. Он выводит server public key из
X25519 private key через уже имеющуюся закреплённую библиотеку `cryptography`,
валидирует endpoint/slot контракт текущей API `WindowManifest` и формирует:

- `node/agent.json` без node credential;
- `node/slots.json` с постоянными server private keys и
  `HeaderProtectionKey`;
- `node/awg/slot-N.conf` с параметрами AWG interface и теми же постоянными
  server/header keys;
- `api/window-manifest.json` с server public keys и `HeaderProtectionKey`, без
  server private keys.

AWG параметры берутся из существующего API `AWG_PARAMETERS`, чтобы профиль
сервера и клиента оставался согласованным с уже принятой реализацией. Каждый
AWG config также содержит секрет; output directory целиком считается секретом.
CLI принимает source только из файла вне checkout с владельцем текущего Linux
пользователя, mode не шире 0600 и непублично изменяемым parent. Создаёт только
новый output directory вне checkout (0700), все файлы — 0600; Windows запуск
отказывает, поскольку этот инструмент не проверяет NTFS ACL. В CLI/evidence
попадают только fixed error codes и имена выходных файлов.

## Проверки и ограничения

Synthetic tests сверяют node/API/AWG ключи и AWG port/profile; приватный ключ
отсутствует в API manifest. Для фиксированного TEST ONLY X25519 key public output
совпал побайтно с закреплённым `awg pubkey` в Kali WSL. Это проверяет derivation и
schema, но не применение config к beta core. Никаких реальных ключей и deploy
не выполнялось. AWG configs пока не потребляются persistent slot bootstrap;
namespace, core, host forwarding/firewall и reboot recovery не реализованы или не
приняты.

# ADR-050 — fail-closed beta node slot preflight

Статус: принято для локального I-030 шаблона; не установлено на beta host.

## Контекст

Beta agent ожидает readiness marker для каждого настроенного слота, а unit ранее
проверял только slot 1. `/run` очищается при reboot, поэтому markers должен заново
создать root-owned bootstrap до запуска daemon.

## Решение

`beta_node_preflight.py` запускается через `ExecStartPre`. Он требует root-owned
private `agent.json`, `slots.json` и readiness directory; проверяет slot count,
валидность ключей, наличие каждого marker и совпадение marker public key с
persistent slot config. Файлы читаются через `O_NOFOLLOW`/`fstat`; ошибки fixed и
не включают key values. ADR-051 добавляет bootstrap, который создаёт эти markers
после применения slot config и закрытия nft gate.

## Проверки и ограничения

Synthetic tests покрывают ключевой validator и fail-closed diagnostics. Полный
root-owned Linux preflight, systemd lifecycle и reboot recovery не приняты на
target host. ADR-051 fake-runner checks — только командный порядок, без kernel
runtime. Не доказывают работоспособность ноды или VPN трафика.

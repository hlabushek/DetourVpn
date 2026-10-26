# ADR-051 — closed AWG slot bootstrap для beta node

Статус: принято для локального I-030 implementation; не установлено и не принято
на выделенном beta host.

## Контекст

`window_daemon.py` принимает только уже работающие pinned AWG cores и закрывает
гейты через namespace fd. Конфиги стабильных слотов и readiness markers должны
переживать restart процесса и пересоздаваться после reboot. Переносить
`infra/staging-v3/runtime.py` нельзя: он содержит staging A адреса, временную
сетевую политику, host firewall/NAT mutation и ограниченный cleanup timer.

## Решение

Добавлены:

- `beta_slot_bootstrap.py`: root-only фиксированные команды; валидирует root-private
  `slots.json` и AWG slot files, exact profile, public key derivation через pinned
  `awg pubkey` и бинарные hashes; создаёт отдельный named netns на slot, запускает
  systemd core template, применяет интерфейсный AWG config, поднимает loopback и
  `dv3sN` с `10.203.71.1/30`, initializes nft gate closed и пишет readiness marker
  только после сверки core PID/namespace inode/public key/port. Не принимает
  shell/path/interface/port от remote API. На partial failure закрывает созданные
  gate/core и пытается удалить только созданные им namespace.
- `detourvpn-beta-core@.service`: запускает pinned userspace core в fixed namespace.
- `detourvpn-beta-slot-bootstrap.service`: создаёт slots при boot и orderly stop;
  agent unit требует bootstrap service, поэтому stop systemd упорядочен обратно.
- `vpn_api.tunnels.awg_profile.AWG_PARAMETERS`: общий pure profile для API,
  offline config compiler и beta bootstrap; новой crypto/network dependency нет.

Mount-оператору bootstrap unit намеренно не задаются `PrivateTmp`/`ProtectSystem`:
`ip netns add` публикует namespace через bind mount `/run/netns`, а private mount
namespace может оставить mount только внутри временного sandbox. Unit ограничен
фиксированным root-owned entrypoint, `CapabilityBoundingSet=CAP_SYS_ADMIN
CAP_NET_ADMIN CAP_KILL`, закрытым device policy, address families и no-new-privileges.
Это требует проверки именно на target systemd и выделенном host.

Изначальный закрытый bootstrap создавал idle cores без veth и host firewall.
Локальный beta network path добавлен отдельно в ADR-052 и не меняет решение о
запрете global forwarding mutation из bootstrap. Hosted use всё ещё требует
отдельной постоянной ноды и проверки network policy.

## Проверки и ограничения

TEST ONLY fake-runner unit tests check schema, exact config, command order and
rollback; they do not execute kernel/network commands. The initial Kali WSL
runtime check ran the bootstrap in a disposable outer mount/network namespace
with real pinned AWG binaries, iproute2 netns, nft gate, config application,
readiness preflight and orderly teardown. The expanded packet-path result is in
ADR-052 and `docs/evidence/i-030-beta-config-local.json`. Only systemd
start/stop was replaced by a test controller; systemd PID-1 was not exercised.
Generated TEST ONLY keys stayed in private `/run` tmpfs. No production or
physical uplink was used.

`systemd-analyze verify` on Kali WSL systemd 259 returned code 0 for the three
units; DrvFS emitted executable/world-writable source metadata warnings. The
Linux root-owned config path, target Linux systemd lifecycle, host boot/reboot,
provider ingress policy, deployed firewall, Caddy deployment and Telegram OTP
remain unverified. No beta host or external infrastructure was changed.

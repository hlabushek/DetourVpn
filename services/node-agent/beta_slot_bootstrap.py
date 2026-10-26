"""Create fixed, isolated beta AWG slots and publish private readiness markers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
import subprocess
import time
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from window_gate import SlotGate

from vpn_api.tunnels.awg_profile import AWG_PARAMETERS

CONFIG = Path("/etc/detourvpn-node/slots.json")
AWG_CONFIGS = Path("/etc/detourvpn-node/awg")
READY = Path("/run/detourvpn-node/slots")
NETNS_DIR = Path("/run/netns")
UAPI_DIR = Path("/run/amneziawg")
IP = "/usr/sbin/ip"
SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_EXECUTOR = "/usr/lib/systemd/systemd-executor"
AWG = Path("/opt/detourvpn-node/current/awg")
CORE = Path("/opt/detourvpn-node/current/amneziawg-go")
NFT = "/usr/sbin/nft"
SYSCTL = "/usr/sbin/sysctl"
SS = "/usr/bin/ss"
AWG_HASH = "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce"
CORE_HASH = "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe"
HOST_TABLE = "detourvpn_beta"
HOST_NAT_TABLE = "detourvpn_beta_nat"
VETH_POOL = ipaddress.IPv4Network("10.203.82.0/26")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LOG_LEVEL": "silent", "LC_ALL": "C"}
NAMESPACE_PREFIX = "detourvpn-beta-slot-"
UNIT_PREFIX = "detourvpn-beta-core@"


class BootstrapError(Exception):
    """Fixed diagnostics without keys, command output or filesystem paths."""


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    slot_id: int = Field(strict=True, ge=1, le=16)
    endpoint: str
    server_private_key: SecretStr = Field(repr=False)
    server_public_key: str = Field(min_length=44, max_length=44, repr=False)
    header_protection_key: SecretStr = Field(repr=False)

    @field_validator("server_private_key", "header_protection_key")
    @classmethod
    def keys_are_canonical(cls, value: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid key") from None
        if (
            len(raw) != 32
            or raw == bytes(32)
            or base64.b64encode(raw).decode() != value.get_secret_value()
        ):
            raise ValueError("Invalid key")
        return value

    @field_validator("server_public_key")
    @classmethod
    def public_key_is_canonical(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid public key") from None
        if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
            raise ValueError("Invalid public key")
        return value

    @field_validator("endpoint")
    @classmethod
    def endpoint_is_fixed_public_ipv4(cls, value: str, info: Any) -> str:
        try:
            host, port_text = value.split(":", 1)
            address = IPv4Address(host)
            slot_id = info.data["slot_id"]
            port = int(port_text)
        except (ValueError, KeyError):
            raise ValueError("Invalid endpoint") from None
        if (
            not address.is_global
            or not port_text.isascii()
            or not port_text.isdecimal()
            or str(port) != port_text
            or port != 51900 + slot_id
        ):
            raise ValueError("Invalid endpoint")
        return value


class Slots(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: int = Field(strict=True, ge=1, le=1)
    slots: list[Slot] = Field(min_length=1, max_length=16)

    @field_validator("slots")
    @classmethod
    def slots_are_contiguous(cls, value: list[Slot]) -> list[Slot]:
        if [slot.slot_id for slot in value] != list(range(1, len(value) + 1)):
            raise ValueError("Slots must be contiguous")
        if len({slot.endpoint.rsplit(":", 1)[0] for slot in value}) != 1:
            raise ValueError("Slots must share one public endpoint address")
        return value


class Ready(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    slot_id: int = Field(strict=True, ge=1, le=16)
    native_pid: int = Field(strict=True, gt=1)
    native_netns_inode: int = Field(strict=True, gt=0)
    server_public_key: str = Field(min_length=44, max_length=44, repr=False)


def _private_read(path: Path, limit: int) -> bytes:
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise BootstrapError("private_file_untrusted")
        for parent in path.parents:
            directory = parent.lstat()
            if (
                not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != 0
                or directory.st_mode & 0o022
            ):
                raise BootstrapError("private_directory_untrusted")
        with os.fdopen(fd, "rb", closefd=True) as source:
            fd = -1
            data = source.read(limit + 1)
        if len(data) != info.st_size:
            raise BootstrapError("private_file_changed")
        return data
    except BootstrapError:
        raise
    except OSError:
        raise BootstrapError("private_file_unavailable") from None
    finally:
        if fd >= 0:
            os.close(fd)


def _private_json(path: Path, limit: int) -> dict[str, Any]:
    try:
        result = json.loads(_private_read(path, limit))
    except (ValueError, UnicodeError):
        raise BootstrapError("private_json_invalid") from None
    if not isinstance(result, dict):
        raise BootstrapError("private_json_invalid")
    return result


def _run(arguments: list[str], *, data: str | None = None, required: bool = True) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed systemctl operation/unit, no shell
            arguments,
            input=data,
            text=True,
            capture_output=True,
            env=ENV,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BootstrapError("fixed_command_unavailable") from None
    if required and result.returncode:
        raise BootstrapError("fixed_command_failed")
    return result.stdout.strip() if result.returncode == 0 else ""


def _artifact(path: Path, expected: str) -> None:
    try:
        for parent in (path, *path.parents):
            info = parent.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or (parent == path and not stat.S_ISREG(info.st_mode))
                or (parent != path and not stat.S_ISDIR(info.st_mode))
                or info.st_uid != 0
                or info.st_mode & 0o022
            ):
                raise BootstrapError("node_artifact_untrusted")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except BootstrapError:
        raise
    except OSError:
        raise BootstrapError("node_artifact_unavailable") from None
    if digest != expected:
        raise BootstrapError("node_artifact_hash_mismatch")


def _namespace(slot_id: int) -> str:
    return f"{NAMESPACE_PREFIX}{slot_id}"


def _unit(slot_id: int) -> str:
    return f"{UNIT_PREFIX}{slot_id}.service"


def _veth(slot_id: int) -> tuple[str, str, str, str]:
    if type(slot_id) is not int or not 1 <= slot_id <= 16:
        raise BootstrapError("invalid_fixed_slot")
    network = list(VETH_POOL.subnets(new_prefix=30))[slot_id - 1]
    host = str(network.network_address + 1)
    outer = str(network.network_address + 2)
    return f"dvbh{slot_id}", host, f"dvbu{slot_id}", outer


def _slot_network_rules(slot: Slot) -> str:
    _hostdev, _host, _outerdev, _outer = _veth(slot.slot_id)
    port = 51900 + slot.slot_id
    endpoint = slot.endpoint.rsplit(":", 1)[0]
    # The gate table remains a separate earlier hook and is the sole window limiter.
    return f"""table inet detourvpn_beta_slot {{
 chain input {{ type filter hook input priority 0; policy drop;
  iifname "lo" accept
  iifname "outer0" udp dport {port} accept
 }}
 chain forward {{ type filter hook forward priority 0; policy drop;
  iifname "dv3s{slot.slot_id}" ip daddr {{
   0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16,
   172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16,
   198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4,
   {endpoint}
  }} drop
  iifname "dv3s{slot.slot_id}" oifname "outer0" ip saddr 10.203.71.2 accept
  iifname "outer0" oifname "dv3s{slot.slot_id}" ct state established,related accept
 }}
}}
table ip detourvpn_beta_slot_nat {{
 chain post {{ type nat hook postrouting priority srcnat; policy accept;
  oifname "outer0" ip saddr 10.203.71.2 masquerade
 }}
}}
"""


def _host_network_preflight(slots: list[Slot]) -> str:
    try:
        if Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() != "1":
            raise BootstrapError("host_ipv4_forwarding_must_be_preconfigured")
        routes = json.loads(_run([IP, "-j", "-4", "route", "get", "1.1.1.1"]))
        if len(routes) != 1:
            raise ValueError
        uplink = routes[0]["dev"]
        source = routes[0]["prefsrc"]
        if not isinstance(uplink, str) or not isinstance(source, str):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", uplink):
            raise BootstrapError("host_uplink_name_invalid")
        endpoint = slots[0].endpoint.rsplit(":", 1)[0]
        if source != endpoint:
            raise BootstrapError("host_endpoint_must_be_uplink_source")
        addresses = json.loads(_run([IP, "-j", "-4", "address", "show", "dev", uplink]))
        local_addresses = {
            address.get("local") for link in addresses for address in link.get("addr_info", [])
        }
        if endpoint not in local_addresses:
            raise BootstrapError("host_endpoint_not_assigned_to_uplink")
        all_routes = json.loads(_run([IP, "-j", "-4", "route", "show", "table", "all"]))
        for route in all_routes:
            destination = route.get("dst")
            if destination and destination != "default":
                try:
                    route_network = ipaddress.ip_network(destination, strict=False)
                except ValueError:
                    raise BootstrapError("host_route_entry_invalid") from None
                if route_network.overlaps(VETH_POOL):
                    raise BootstrapError("beta_veth_route_conflict")
        all_addresses = json.loads(_run([IP, "-j", "-4", "address", "show"]))
        for link in all_addresses:
            for address in link.get("addr_info", []):
                try:
                    interface = ipaddress.ip_interface(
                        address["local"] + "/" + str(address["prefixlen"])
                    )
                except (KeyError, TypeError, ValueError):
                    raise BootstrapError("host_address_entry_invalid") from None
                if interface.network.overlaps(VETH_POOL):
                    raise BootstrapError("beta_veth_address_conflict")
        present = json.loads(_run([NFT, "-j", "list", "tables"]))
        for namespace, table in (("inet", HOST_TABLE), ("ip", HOST_NAT_TABLE)):
            if any(
                item.get("table", {}).get("family") == namespace
                and item.get("table", {}).get("name") == table
                for item in present.get("nftables", [])
            ):
                raise BootstrapError("beta_host_firewall_table_exists")
        if _run([SS, "-H", "-lun", "sport", ">=", "51901", "and", "sport", "<=", "51916"]):
            raise BootstrapError("beta_awg_port_in_use")
        for slot in slots:
            hostdev, _host, peerdev, _outer = _veth(slot.slot_id)
            for device in (hostdev, peerdev):
                if _run([IP, "-j", "link", "show", "dev", device], required=False):
                    raise BootstrapError("beta_veth_exists")
    except BootstrapError:
        raise
    except KeyError:
        raise BootstrapError("host_network_route_field_missing") from None
    except OSError:
        raise BootstrapError("host_network_state_unavailable") from None
    except TypeError:
        raise BootstrapError("host_network_state_invalid") from None
    except ValueError:
        raise BootstrapError("host_network_address_invalid") from None
    return uplink


def _host_firewall_program(slots: list[Slot], uplink: str) -> str:
    endpoint = slots[0].endpoint.rsplit(":", 1)[0]
    input_drops: list[str] = []
    forwarding: list[str] = []
    dnat: list[str] = []
    masquerade: list[str] = []
    for slot in slots:
        slot_id = slot.slot_id
        hostdev, _host, _peerdev, outer = _veth(slot_id)
        port = 51900 + slot_id
        input_drops.append(f'  iifname "{hostdev}" drop')
        forwarding.extend(
            [
                f'  iifname "{hostdev}" oifname "{uplink}" ip saddr {outer} accept',
                (
                    f'  iifname "{hostdev}" oifname "{uplink}" ip saddr {endpoint} '
                    "ct state established,related ct status dnat accept"
                ),
                (
                    f'  iifname "{uplink}" oifname "{hostdev}" ip daddr {outer} '
                    f"udp dport {port} ct status dnat accept"
                ),
                (
                    f'  iifname "{uplink}" oifname "{hostdev}" ip daddr {outer} '
                    "ct state established,related accept"
                ),
                f'  iifname "{hostdev}" drop',
                f'  oifname "{hostdev}" drop',
            ]
        )
        dnat.append(
            f'  iifname "{uplink}" ip daddr {endpoint} udp dport {port} dnat to {outer}:{port}'
        )
        masquerade.append(f'  oifname "{uplink}" ip saddr {outer} masquerade')
    return (
        f"table inet {HOST_TABLE} {{\n"
        " chain input { type filter hook input priority -10; policy accept;\n"
        + "\n".join(input_drops)
        + "\n }\n chain forward { type filter hook forward priority -10; policy accept;\n"
        + "\n".join(forwarding)
        + "\n }\n}\n"
        + f"table ip {HOST_NAT_TABLE} {{\n"
        + " chain pre { type nat hook prerouting priority dstnat; policy accept;\n"
        + "\n".join(dnat)
        + "\n }\n chain post { type nat hook postrouting priority srcnat; policy accept;\n"
        + "\n".join(masquerade)
        + "\n }\n}\n"
    )


def _create_veth(slot: Slot, namespace: str) -> None:
    slot_id = slot.slot_id
    hostdev, host, peerdev, outer = _veth(slot_id)
    _run([IP, "link", "add", hostdev, "type", "veth", "peer", "name", peerdev])
    _run([IP, "link", "set", peerdev, "netns", namespace])
    _run([IP, "-n", namespace, "link", "set", peerdev, "name", "outer0"])
    _run([IP, "address", "add", host + "/30", "dev", hostdev])
    _run([IP, "link", "set", hostdev, "up"])
    _run([IP, "-n", namespace, "address", "add", outer + "/30", "dev", "outer0"])
    _run([IP, "-n", namespace, "link", "set", "outer0", "up"])
    _run([IP, "-n", namespace, "route", "add", "default", "via", host])
    _run(
        [
            IP,
            "netns",
            "exec",
            namespace,
            SYSCTL,
            "-q",
            "-w",
            "net.ipv4.ip_forward=1",
            "net.ipv6.conf.all.disable_ipv6=1",
            "net.ipv6.conf.default.disable_ipv6=1",
        ]
    )
    # nft JSON/CLI inputs are generated solely from validated fixed slot and host values.
    _run([IP, "netns", "exec", namespace, NFT, "-f", "-"], data=_slot_network_rules(slot))


def _remove_host_firewall() -> None:
    _run([NFT, "delete", "table", "inet", HOST_TABLE])
    _run([NFT, "delete", "table", "ip", HOST_NAT_TABLE])


def _remove_host_veth(slot_id: int) -> None:
    hostdev, _host, _peerdev, _outer = _veth(slot_id)
    if _run([IP, "link", "show", hostdev], required=False):
        _run([IP, "link", "delete", hostdev])


def _config(slot_id: int) -> bytes:
    return _private_read(AWG_CONFIGS / f"slot-{slot_id}.conf", 4096)


def expected_awg_config(slot: Slot) -> bytes:
    private = slot.server_private_key.get_secret_value()
    protection = slot.header_protection_key.get_secret_value()
    return (
        "[Interface]\n"
        f"PrivateKey = {private}\n"
        f"ListenPort = {51900 + slot.slot_id}\n"
        + AWG_PARAMETERS
        + f"HeaderProtectionKey = {protection}\n"
    ).encode("ascii")


def _load_slots() -> list[Slot]:
    _artifact(AWG, AWG_HASH)
    try:
        parsed = Slots.model_validate_json(_private_read(CONFIG, 65536))
    except BootstrapError:
        raise
    except ValidationError:
        raise BootstrapError("slot_configuration_invalid") from None
    for slot in parsed.slots:
        config = _config(slot.slot_id)
        if not hmac.compare_digest(config, expected_awg_config(slot)):
            raise BootstrapError("slot_awg_config_conflict")
        public = _run([str(AWG), "pubkey"], data=slot.server_private_key.get_secret_value() + "\n")
        if public != slot.server_public_key:
            raise BootstrapError("slot_public_key_conflict")
        if not slot.endpoint.endswith(f":{51900 + slot.slot_id}"):
            raise BootstrapError("slot_endpoint_conflict")
    return parsed.slots


def _service_active(unit: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603 - fixed systemctl operation/unit, no shell
            [SYSTEMCTL, "is-active", "--quiet", unit],
            capture_output=True,
            text=True,
            env=ENV,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BootstrapError("systemd_unavailable") from None
    if result.returncode == 4:
        raise BootstrapError("systemd_unit_unavailable")
    if result.returncode not in (0, 3):
        raise BootstrapError("systemd_state_unknown")
    return result.returncode == 0


def _preflight_start(slots: list[Slot]) -> str:
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise BootstrapError("linux_root_required")
    _artifact(AWG, AWG_HASH)
    _artifact(CORE, CORE_HASH)
    if READY.exists() or READY.is_symlink():
        raise BootstrapError("readiness_path_exists")
    for slot in slots:
        if (NETNS_DIR / _namespace(slot.slot_id)).exists():
            raise BootstrapError("slot_namespace_exists")
        if Path(f"/run/amneziawg/dv3s{slot.slot_id}.sock").exists():
            raise BootstrapError("slot_uapi_exists")
        if _service_active(_unit(slot.slot_id)):
            raise BootstrapError("slot_core_already_active")
    return _host_network_preflight(slots)


def _ensure_uapi_dir() -> None:
    try:
        if not UAPI_DIR.exists():
            UAPI_DIR.mkdir(mode=0o755)
        info = UAPI_DIR.lstat()
    except OSError:
        raise BootstrapError("uapi_directory_unavailable") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise BootstrapError("uapi_directory_untrusted")


def _write_ready(slot: Slot, pid: int, inode: int) -> None:
    if not READY.exists():
        READY.mkdir(mode=0o700, parents=False, exist_ok=False)
    ready_info = READY.lstat()
    if not stat.S_ISDIR(ready_info.st_mode) or ready_info.st_uid != 0 or ready_info.st_mode & 0o077:
        raise BootstrapError("readiness_directory_untrusted")
    payload = (
        json.dumps(
            {
                "slot_id": slot.slot_id,
                "native_pid": pid,
                "native_netns_inode": inode,
                "server_public_key": slot.server_public_key,
            },
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    path = READY / f"{slot.slot_id}.json"
    fd = -1
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise BootstrapError("readiness_write_failed") from None
    finally:
        if fd >= 0:
            os.close(fd)


def _is_systemd_exec_transition(executable: str, argv: list[bytes]) -> bool:
    """Recognize only systemd's short-lived fixed `ip netns exec` launcher."""
    return executable == SYSTEMD_EXECUTOR and argv == [b"(ip)"]


def _wait_core(slot: Slot, inode: int) -> tuple[int, int]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pid_text = _run([SYSTEMCTL, "show", "--property=MainPID", "--value", _unit(slot.slot_id)])
        try:
            pid = int(pid_text)
        except ValueError:
            pid = 0
        if pid > 1:
            try:
                process = Path("/proc") / str(pid)
                if process.stat().st_uid != 0:
                    raise BootstrapError("slot_core_identity_conflict")
                executable = os.readlink(process / "exe")
                argv = (process / "cmdline").read_bytes().split(b"\0")
                if argv and argv[-1] == b"":
                    argv.pop()
                process_inode = (process / "ns/net").stat().st_ino
                if process_inode != inode:
                    if _is_systemd_exec_transition(executable, argv):
                        time.sleep(0.1)
                        continue
                    raise BootstrapError("slot_core_identity_conflict")
                if hashlib.sha256((process / "exe").read_bytes()).hexdigest() != CORE_HASH:
                    raise BootstrapError("slot_core_identity_conflict")
                return pid, inode
            except OSError:
                pass
        time.sleep(0.1)
    raise BootstrapError("slot_core_start_timeout")


def _gate(slot_id: int) -> SlotGate:
    fd = os.open(NETNS_DIR / _namespace(slot_id), os.O_RDONLY)
    try:
        return SlotGate(slot_id, fd)
    finally:
        os.close(fd)


def start_slots(slots: list[Slot] | None = None) -> int:
    slots = slots if slots is not None else _load_slots()
    uplink = _preflight_start(slots)
    created_namespaces: list[int] = []
    started_cores: list[int] = []
    host_firewall_installed = False
    try:
        _ensure_uapi_dir()
        _run([NFT, "-f", "-"], data=_host_firewall_program(slots, uplink))
        host_firewall_installed = True
        for slot in slots:
            slot_id = slot.slot_id
            namespace = _namespace(slot_id)
            interface = f"dv3s{slot_id}"
            _run([IP, "netns", "add", namespace])
            created_namespaces.append(slot_id)
            _run([IP, "-n", namespace, "link", "set", "lo", "up"])
            _create_veth(slot, namespace)
            _run([SYSTEMCTL, "start", _unit(slot_id)])
            started_cores.append(slot_id)
            inode = (NETNS_DIR / namespace).stat().st_ino
            pid, _ = _wait_core(slot, inode)
            _run(
                [
                    IP,
                    "netns",
                    "exec",
                    namespace,
                    str(AWG),
                    "setconf",
                    interface,
                    str(AWG_CONFIGS / f"slot-{slot_id}.conf"),
                ]
            )
            _run([IP, "-n", namespace, "address", "add", "10.203.71.1/30", "dev", interface])
            _run([IP, "-n", namespace, "link", "set", interface, "mtu", "1280", "up"])
            gate = _gate(slot_id)
            try:
                gate.initialize_closed()
            finally:
                gate.dispose()
            observed_public = _run(
                [IP, "netns", "exec", namespace, str(AWG), "show", interface, "public-key"]
            )
            observed_port = _run(
                [IP, "netns", "exec", namespace, str(AWG), "show", interface, "listen-port"]
            )
            if observed_public != slot.server_public_key or observed_port != str(51900 + slot_id):
                raise BootstrapError("slot_awg_identity_conflict")
            _write_ready(slot, pid, inode)
        return len(slots)
    except Exception:
        _rollback(created_namespaces, started_cores)
        if host_firewall_installed and not any(
            (NETNS_DIR / _namespace(slot_id)).exists() for slot_id in created_namespaces
        ):
            _remove_host_firewall()
        raise


def _rollback(namespaces: list[int], cores: list[int]) -> None:
    for slot_id in reversed(cores):
        _run([SYSTEMCTL, "stop", _unit(slot_id)], required=False)
    for slot_id in reversed(namespaces):
        path = READY / f"{slot_id}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        _run([IP, "netns", "delete", _namespace(slot_id)], required=False)
        _remove_host_veth(slot_id)
    try:
        READY.rmdir()
    except OSError:
        pass


def _read_ready(slot_id: int, *, allow_stopped_core: bool = False) -> Ready:
    try:
        ready = Ready.model_validate(_private_json(READY / f"{slot_id}.json", 4096))
    except BootstrapError:
        raise
    except ValidationError:
        raise BootstrapError("slot_readiness_invalid") from None
    if ready.slot_id != slot_id:
        raise BootstrapError("slot_readiness_conflict")
    namespace = NETNS_DIR / _namespace(slot_id)
    try:
        if namespace.stat().st_ino != ready.native_netns_inode:
            raise BootstrapError("slot_namespace_conflict")
        if not _service_active(_unit(slot_id)):
            if allow_stopped_core:
                return ready
            raise BootstrapError("slot_core_identity_unavailable")
        process = Path("/proc") / str(ready.native_pid)
        if (
            process.stat().st_uid != 0
            or (process / "ns/net").stat().st_ino != ready.native_netns_inode
        ):
            raise BootstrapError("slot_core_identity_conflict")
        if hashlib.sha256((process / "exe").read_bytes()).hexdigest() != CORE_HASH:
            raise BootstrapError("slot_core_identity_conflict")
    except OSError:
        if allow_stopped_core and not _service_active(_unit(slot_id)):
            return ready
        raise BootstrapError("slot_core_identity_unavailable") from None
    return ready


def stop_slots(slots: list[Slot] | None = None) -> int:
    slots = slots if slots is not None else _load_slots()
    if not READY.exists():
        if any((NETNS_DIR / _namespace(slot.slot_id)).exists() for slot in slots):
            raise BootstrapError("slot_namespace_without_readiness")
        if any(_service_active(_unit(slot.slot_id)) for slot in slots):
            raise BootstrapError("slot_core_without_readiness")
        return 0
    ready = [_read_ready(slot.slot_id, allow_stopped_core=True) for slot in slots]
    if any(
        marker.server_public_key != slot.server_public_key
        for marker, slot in zip(ready, slots, strict=True)
    ):
        raise BootstrapError("slot_readiness_conflict")
    for slot in slots:
        gate = _gate(slot.slot_id)
        try:
            gate.close()
        finally:
            gate.dispose()
    for slot in slots:
        _run([SYSTEMCTL, "stop", _unit(slot.slot_id)])
    for slot, marker in zip(slots, ready, strict=True):
        _run([IP, "netns", "delete", _namespace(slot.slot_id)])
        _remove_host_veth(slot.slot_id)
        (READY / f"{marker.slot_id}.json").unlink()
    READY.rmdir()
    _remove_host_firewall()
    return len(slots)


def main(arguments: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    args = parser.parse_args(arguments)
    stage = "configuration"
    try:
        slots = _load_slots()
        stage = args.action
        count = start_slots(slots) if args.action == "start" else stop_slots(slots)
    except BootstrapError as error:
        print(json.dumps({"error": str(error), "stage": stage}), flush=True)
        return 1
    except Exception:
        print(json.dumps({"error": "beta_slot_bootstrap_failed", "stage": stage}), flush=True)
        return 1
    print(json.dumps({f"{args.action}ed_slots": count}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

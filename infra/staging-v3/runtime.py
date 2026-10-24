"""Root-owned fixed I-028 operator actions. No caller-selected shell/path/network arguments."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path("/opt/detourvpn-i028")
RUN = Path("/run/detourvpn-i028")
NODE = Path("/run/detourvpn-node/slots")
AWG = Path("/opt/detourvpn-node/current/awg")
CORE = Path("/opt/detourvpn-node/current/amneziawg-go")
CONFIG_DIR = Path("/etc/detourvpn-node")
STATE_DIR = Path("/var/lib/detourvpn-node")
GRANT = Path("/etc/sudoers.d/detourvpn-i028")
IP = "/usr/sbin/ip"
IPT = "/usr/sbin/iptables"
HOST = "45.10.166.106"
IFACE = "ens3"
SLOTS = (1, 2)
PARAMETERS = (
    "Jc = 4\nJmin = 64\nJmax = 96\nS1 = 16\nS2 = 24\nS3 = 32\nS4 = 40\n"
    "H1 = 101234\nH2 = 202345\nH3 = 303456\nH4 = 404567\n"
)
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LOG_LEVEL": "silent"}


class Failure(Exception):
    """Fixed errors only, never command output or key/config data."""


def call(args: list[str], data: str | None = None, *, required: bool = True) -> str:
    if args[0] == IPT:
        args = [IPT, "-w", "5", *args[1:]]
    result = subprocess.run(  # noqa: S603 - enumerated operator commands, no shell.
        args, input=data, text=True, capture_output=True, env=ENV, timeout=20, check=False
    )
    if required and result.returncode:
        raise Failure("operator_command_failed")
    return result.stdout.strip() if result.returncode == 0 else ""


def write_private(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def addresses(slot: int) -> tuple[str, str]:
    if type(slot) is not int or slot not in SLOTS:
        raise Failure("invalid_fixed_slot")
    return f"10.203.80.{4 * (slot - 1) + 1}", f"10.203.80.{4 * (slot - 1) + 2}"


def isolation(slot: int) -> str:
    addresses(slot)
    return f"""table inet detour_i028_isolation {{
 chain input {{ type filter hook input priority 0; policy drop;
  iifname "lo" accept
  iifname "outer0" udp dport {51900 + slot} accept
 }}
 chain forward {{ type filter hook forward priority 0; policy drop;
  iifname "dv3s{slot}" ip daddr {{
   0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16,
   172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16,
   198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4,
   45.10.166.106, 85.209.132.205
  }} drop
  iifname "dv3s{slot}" oifname "outer0" ip saddr 10.203.71.2 accept
  iifname "outer0" oifname "dv3s{slot}" ct state established,related accept
 }}
}}
table ip detour_i028_nat {{
 chain post {{ type nat hook postrouting priority srcnat; policy accept;
  oifname "outer0" ip saddr 10.203.71.2 masquerade
 }}
}}
"""


def host_rules() -> list[tuple[str, list[str]]]:
    result = []
    for slot in SLOTS:
        _, outer = addresses(slot)
        result.extend(
            [
                (
                    "nat",
                    [
                        "DV3I028D",
                        "-p",
                        "udp",
                        "--dport",
                        str(51900 + slot),
                        "-j",
                        "DNAT",
                        "--to-destination",
                        outer + ":" + str(51900 + slot),
                    ],
                ),
                ("nat", ["DV3I028S", "-s", outer + "/32", "-o", IFACE, "-j", "MASQUERADE"]),
                (
                    "filter",
                    [
                        "DV3I028F",
                        "-i",
                        f"dv3h{slot}",
                        "-o",
                        IFACE,
                        "-s",
                        outer + "/32",
                        "-j",
                        "ACCEPT",
                    ],
                ),
                (
                    "filter",
                    [
                        "DV3I028F",
                        "-i",
                        IFACE,
                        "-o",
                        f"dv3h{slot}",
                        "-d",
                        outer + "/32",
                        "-p",
                        "udp",
                        "--dport",
                        str(51900 + slot),
                        "-j",
                        "ACCEPT",
                    ],
                ),
                (
                    "filter",
                    [
                        "DV3I028F",
                        "-i",
                        IFACE,
                        "-o",
                        f"dv3h{slot}",
                        "-d",
                        outer + "/32",
                        "-m",
                        "conntrack",
                        "--ctstate",
                        "ESTABLISHED,RELATED",
                        "-j",
                        "ACCEPT",
                    ],
                ),
                ("filter", ["DV3I028F", "-i", f"dv3h{slot}", "-j", "DROP"]),
                ("filter", ["DV3I028F", "-o", f"dv3h{slot}", "-j", "DROP"]),
            ]
        )
    return result


JUMPS = (
    (
        "nat",
        [
            "PREROUTING",
            "-i",
            IFACE,
            "-d",
            HOST,
            "-p",
            "udp",
            "--dport",
            "51901:51902",
            "-j",
            "DV3I028D",
        ],
    ),
    ("nat", ["POSTROUTING", "-s", "10.203.80.0/28", "-o", IFACE, "-j", "DV3I028S"]),
    ("filter", ["FORWARD", "-j", "DV3I028F"]),
    ("filter", ["INPUT", "-i", "dv3h1", "-m", "comment", "--comment", "detour-i028", "-j", "DROP"]),
    ("filter", ["INPUT", "-i", "dv3h2", "-m", "comment", "--comment", "detour-i028", "-j", "DROP"]),
)
CHAINS = (("nat", "DV3I028D"), ("nat", "DV3I028S"), ("filter", "DV3I028F"))


def module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    if spec is None or spec.loader is None:
        raise Failure("operator_module_missing")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def gate(slot: int) -> Any:
    addresses(slot)
    fd = os.open(f"/run/netns/dv3slot{slot}", os.O_RDONLY)
    try:
        return module("window_gate").SlotGate(slot, fd)
    finally:
        os.close(fd)


def unit(name: str, command: list[str], *, tls: bool = False) -> None:
    args = [
        "/usr/bin/systemd-run",
        "--quiet",
        "--collect",
        "--unit",
        "detourvpn-i028-" + name,
        "-p",
        "Type=exec",
        "-p",
        "KillMode=control-group",
        "-p",
        "StandardOutput=null",
        "-p",
        "StandardError=null",
        "-p",
        "RuntimeMaxSec=1800",
        "-p",
        "MemoryMax=128M",
        "-p",
        "TasksMax=64",
        "--setenv=LOG_LEVEL=silent",
    ]
    if tls:
        args += ["--setenv=SSL_CERT_FILE=" + str(RUN / "localhost.crt")]
    call([*args, "--", *command])


def preflight() -> None:
    if os.geteuid() != 0:
        raise Failure("root_required")
    for path in (RUN, NODE.parent, CONFIG_DIR, STATE_DIR):
        if path.exists() or path.is_symlink():
            raise Failure("existing_runtime_refused")
    local = json.loads(call([IP, "-j", "-4", "address", "show", "dev", IFACE]))
    if not any(a.get("local") == HOST for row in local for a in row.get("addr_info", [])):
        raise Failure("wrong_staging_host")
    for row in json.loads(call([IP, "-j", "-4", "route", "show", "table", "all"])):
        dst = row.get("dst", "default")
        if dst != "default" and ipaddress.ip_network(dst, strict=False).overlaps(
            ipaddress.ip_network("10.203.80.0/28")
        ):
            raise Failure("slot_route_conflict")
    if call(["/usr/bin/ss", "-H", "-ltn", "sport = :443"]):
        raise Failure("loopback_tls_port_in_use")
    if call(["/usr/bin/ss", "-H", "-lun", "sport >= :51901 and sport <= :51902"]):
        raise Failure("slot_port_in_use")
    if any(
        "flowtable" in row
        for row in json.loads(call(["/usr/sbin/nft", "-j", "list", "ruleset"]))["nftables"]
    ):
        raise Failure("host_flow_offload_requires_separate_review")
    for table, chain in CHAINS:
        if call([IPT, "-t", table, "-S", chain], required=False):
            raise Failure("operator_chain_exists")
    for slot in SLOTS:
        if Path(f"/run/netns/dv3slot{slot}").exists() or call(
            [IP, "-j", "link", "show", f"dv3h{slot}"], required=False
        ):
            raise Failure("operator_interface_exists")


def prepare() -> dict[str, object]:
    preflight()
    RUN.mkdir(mode=0o700)
    baseline = {
        "deadline": int(time.time()) + 1800,
        "forward": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip(),
    }
    write_private(RUN / "baseline.json", json.dumps(baseline))
    # Schedule cleanup BEFORE any exposure or privilege grant.
    call(
        [
            "/usr/bin/systemd-run",
            "--quiet",
            "--unit",
            "detourvpn-i028-timeout",
            "--on-active=30m",
            "--timer-property=AccuracySec=1s",
            "--",
            "/usr/local/libexec/detourvpn-i028-control",
            "stop",
        ]
    )
    try:
        for directory in (CONFIG_DIR, STATE_DIR, NODE.parent, NODE):
            directory.mkdir(mode=0o700)
        write_private(RUN / "firewall-before.txt", call(["/usr/sbin/iptables-save"]))
        Path("/proc/sys/net/ipv4/ip_forward").write_text("1\n")
        for table, chain in CHAINS:
            call([IPT, "-t", table, "-N", chain])
        slots = []
        for slot in SLOTS:
            inside, outer = addresses(slot)
            namespace, hostdev, interface = f"dv3slot{slot}", f"dv3h{slot}", f"dv3s{slot}"
            call([IP, "netns", "add", namespace])
            call([IP, "link", "add", hostdev, "type", "veth", "peer", "name", f"dv3u{slot}"])
            call([IP, "link", "set", f"dv3u{slot}", "netns", namespace])
            call([IP, "-n", namespace, "link", "set", f"dv3u{slot}", "name", "outer0"])
            call([IP, "address", "add", inside + "/30", "dev", hostdev])
            call([IP, "link", "set", hostdev, "up"])
            call([IP, "-n", namespace, "address", "add", outer + "/30", "dev", "outer0"])
            call([IP, "-n", namespace, "link", "set", "outer0", "up"])
            call([IP, "-n", namespace, "link", "set", "lo", "up"])
            call([IP, "-n", namespace, "route", "add", "default", "via", inside])
            call(
                [
                    IP,
                    "netns",
                    "exec",
                    namespace,
                    "/usr/sbin/sysctl",
                    "-q",
                    "-w",
                    "net.ipv4.ip_forward=1",
                    "net.ipv6.conf.all.disable_ipv6=1",
                ]
            )
            call([IP, "netns", "exec", namespace, "/usr/sbin/nft", "-f", "-"], isolation(slot))
            current = gate(slot)
            try:
                current.initialize_closed()
            finally:
                current.dispose()
            unit("core" + str(slot), [IP, "netns", "exec", namespace, str(CORE), "-f", interface])
            for _ in range(100):
                if Path(f"/run/amneziawg/{interface}.sock").exists():
                    break
                time.sleep(0.05)
            else:
                raise Failure("core_start_timeout")
            private = call([str(AWG), "genkey"])
            public = call([str(AWG), "pubkey"], private + "\n")
            protection = call([str(AWG), "genkey"])
            config = RUN / f"server-{slot}.conf"
            write_private(
                config,
                f"[Interface]\nPrivateKey = {private}\nListenPort = {51900 + slot}\n"
                + PARAMETERS
                + "HeaderProtectionKey = "
                + protection
                + "\n",
            )
            call([str(AWG), "setconf", interface, str(config)])
            config.unlink()
            call([IP, "-n", namespace, "address", "add", "10.203.71.1/30", "dev", interface])
            call([IP, "-n", namespace, "link", "set", interface, "mtu", "1280", "up"])
            pid = int(
                call(
                    [
                        "/usr/bin/systemctl",
                        "show",
                        "-p",
                        "MainPID",
                        "--value",
                        f"detourvpn-i028-core{slot}.service",
                    ]
                )
            )
            write_private(
                NODE / f"{slot}.json",
                json.dumps(
                    {
                        "slot_id": slot,
                        "native_pid": pid,
                        "native_netns_inode": Path(f"/proc/{pid}/ns/net").stat().st_ino,
                        "server_public_key": public,
                    }
                ),
            )
            slots.append(
                {
                    "slot_id": slot,
                    "server_public_key": public,
                    "header_protection_key": protection,
                    "endpoint": f"{HOST}:{51900 + slot}",
                }
            )
        write_private(RUN / "slots.json", json.dumps(slots))
        for table, rule in host_rules():
            call([IPT, "-t", table, "-A", *rule])
        for table, rule in JUMPS:
            call([IPT, "-t", table, "-I", rule[0], "1", *rule[1:]])
        call(
            [
                "/usr/bin/openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-keyout",
                str(RUN / "localhost.key"),
                "-out",
                str(RUN / "localhost.crt"),
            ]
        )
        unit("relay", [str(ROOT / ".venv/bin/python"), str(ROOT / "tls_relay.py")])
        return status()
    except BaseException:
        cleanup()
        raise


def status() -> dict[str, object]:
    if (STATE_DIR / "i028-cleanup.json").is_file():
        return {"prepared": False, "cleanup_complete": True}
    if not (RUN / "baseline.json").is_file():
        return {"prepared": False}
    baseline = json.loads((RUN / "baseline.json").read_text())
    live_slots = []
    peer_counts = []
    native_transfer_bytes = []
    for slot in SLOTS:
        if Path(f"/run/netns/dv3slot{slot}").exists():
            current = gate(slot)
            try:
                if current.is_live():
                    live_slots.append(slot)
            finally:
                current.dispose()
            peer_counts.append(len(call([str(AWG), "show", f"dv3s{slot}", "peers"]).splitlines()))
            rows = call([str(AWG), "show", f"dv3s{slot}", "transfer"]).splitlines()
            values = [row.split() for row in rows]
            native_transfer_bytes.append(
                {
                    "slot": slot,
                    "received": str(sum(int(row[1]) for row in values)),
                    "sent": str(sum(int(row[2]) for row in values)),
                }
            )
    return {
        "prepared": True,
        "remaining_seconds": max(0, baseline["deadline"] - int(time.time())),
        "slot_count": len(list(NODE.glob("*.json"))),
        "live_slots": live_slots,
        "peer_counts": peer_counts,
        "native_transfer_bytes": native_transfer_bytes,
        "agent_active": call(
            ["/usr/bin/systemctl", "is-active", "detourvpn-i028-agent.service"], required=False
        )
        == "active",
    }


def configure(raw: bytes) -> None:
    if len(raw) > 512:
        raise Failure("configuration_oversized")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"node_id", "credential"}:
        raise Failure("configuration_fields_forbidden")
    node_id = str(UUID(value["node_id"]))
    credential = value["credential"]
    if not isinstance(credential, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
        raise Failure("invalid_node_credential")
    content = json.dumps({"node_id": node_id, "api_origin": "https://localhost", "slot_count": 2})
    config = CONFIG_DIR / "agent.json"
    secret = CONFIG_DIR / "credential"
    if config.exists() or secret.exists():
        if config.read_text() != content or secret.read_text() != credential:
            raise Failure("existing_agent_identity_conflict")
        return
    write_private(config, content)
    write_private(secret, credential)


def disarm_timeout() -> bool:
    # Stop only the timer, never its service: cleanup may itself be that service.
    timer = "detourvpn-i028-timeout.timer"
    call(["/usr/bin/systemctl", "stop", timer], required=False)
    # is-active exits 3 when inactive; call intentionally suppresses output on
    # nonzero status. show returns the property with a successful status instead.
    state = call(
        ["/usr/bin/systemctl", "show", "--property=ActiveState", "--value", timer], required=False
    )
    return state in ("inactive", "failed")


def cleanup() -> dict[str, object]:
    gate_close_errors = 0
    if (STATE_DIR / "i028-cleanup.json").is_file():
        return {
            "cleanup_complete": disarm_timeout(),
            "journal_preserved": True,
            "temporary_grant_removed": True,
        }
    if not (RUN / "baseline.json").is_file():
        return {"cleanup_complete": not RUN.exists()}
    baseline = json.loads((RUN / "baseline.json").read_text())
    # Deny both directions at the existing dedicated host hook before waiting for
    # an agent HTTP request or native shutdown; late grants cannot reopen exposure.
    call([IPT, "-t", "filter", "-F", "DV3I028F"], required=False)
    for slot in SLOTS:
        for direction in ("-i", "-o"):
            call(
                [IPT, "-t", "filter", "-A", "DV3I028F", direction, f"dv3h{slot}", "-j", "DROP"],
                required=False,
            )
    call(["/usr/bin/systemctl", "stop", "detourvpn-i028-agent.service"], required=False)
    for slot in SLOTS:
        if Path(f"/run/netns/dv3slot{slot}").exists():
            try:
                current = gate(slot)
                try:
                    current.initialize_closed()
                finally:
                    current.dispose()
            except Exception:
                gate_close_errors += 1  # Still remove ingress and stop every core; validate below.
    for table, rule in reversed(JUMPS):
        call([IPT, "-t", table, "-D", *rule], required=False)
    for name in ("relay", "core1", "core2"):
        call(["/usr/bin/systemctl", "stop", "detourvpn-i028-" + name + ".service"], required=False)
    for table, chain in CHAINS:
        call([IPT, "-t", table, "-F", chain], required=False)
        call([IPT, "-t", table, "-X", chain], required=False)
    for slot in SLOTS:
        call([IP, "link", "delete", f"dv3h{slot}"], required=False)
        call([IP, "netns", "delete", f"dv3slot{slot}"], required=False)
    if (
        baseline["forward"] != "1"
        and Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
    ):
        Path("/proc/sys/net/ipv4/ip_forward").write_text(baseline["forward"] + "\n")
    if any(Path(f"/run/netns/dv3slot{s}").exists() for s in SLOTS):
        raise Failure("namespace_cleanup_unconfirmed")
    if any(call([IPT, "-t", table, "-S", chain], required=False) for table, chain in CHAINS):
        raise Failure("firewall_cleanup_unconfirmed")
    GRANT.unlink(missing_ok=True)
    for path in (CONFIG_DIR, NODE.parent):
        if path.is_symlink() or path.name not in ("detourvpn-node", "lab-config"):
            raise Failure("unsafe_cleanup_target")
        if path.exists():
            shutil.rmtree(path)
    # Private aggregate journal survives for operator evidence; never erase unknown receipts.
    destination = STATE_DIR / "i028-cleanup.json"
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    write_private(
        destination, json.dumps({"cleanup_complete": True, "measured_at": int(time.time())})
    )
    for secret in ("localhost.key", "localhost.crt", "slots.json"):
        (RUN / secret).unlink(missing_ok=True)
    disarmed = disarm_timeout()
    return {
        "cleanup_complete": disarmed,
        "timeout_timer_disarmed": disarmed,
        "journal_preserved": True,
        "temporary_grant_removed": True,
        "gate_close_errors_before_core_stop": gate_close_errors,
    }


def main() -> int:
    try:
        if os.geteuid() != 0 or len(sys.argv) != 2:
            raise Failure("fixed_root_action_required")
        operation = sys.argv[1]
        if operation == "prepare":
            result = prepare()
        elif operation == "stop":
            result = cleanup()
        elif operation == "status":
            result = status()
        else:
            info = status()
            remaining = info.get("remaining_seconds")
            if not info.get("prepared") or not isinstance(remaining, int) or remaining <= 0:
                raise Failure("staging_deadline_expired")
            if operation == "manifest":
                result = {"slots": json.loads((RUN / "slots.json").read_text())}
            elif operation == "configure":
                configure(sys.stdin.buffer.read(513))
                result = {"configured": True}
            elif operation == "start-agent":
                unit(
                    "agent",
                    [str(ROOT / ".venv/bin/python"), str(ROOT / "window_daemon.py")],
                    tls=True,
                )
                result = {"agent_started": True}
            elif operation == "stop-agent":
                call(["/usr/bin/systemctl", "stop", "detourvpn-i028-agent.service"])
                result = {"agent_stopped": True}
            elif operation == "crash-agent":
                call(
                    [
                        "/usr/bin/systemctl",
                        "kill",
                        "--signal=SIGKILL",
                        "--kill-whom=all",
                        "detourvpn-i028-agent.service",
                    ]
                )
                result = {"agent_crashed": True}
            else:
                raise Failure("action_forbidden")
        print(json.dumps(result), flush=True)
        return 0
    except Exception:
        print('{"error":"i028_operator_action_failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only readiness check of a candidate beta VPN node host (ADR-094, ADR-052).

Connects with pinned SSH (the host key must already be in known_hosts; verify the fingerprint in
the provider console first), runs one fixed read-only program and prints a sanitized verdict:
check names and pass/fail, never addresses, hostnames or raw output. It changes nothing on the
host. A pass is a precondition for provisioning, not evidence of a working tunnel.
"""

# E501 is disabled because the file embeds one fixed remote program.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.beta_nodes import BetaNode, InventoryError, load_inventory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "infra" / "beta" / "nodes.json"
# Same advisory thresholds the staging operator uses (infra/staging-v3/install.sh); they are
# not universal capacity targets.
MIN_ROOT_FREE_KB = 1_500_000
MIN_MEM_AVAILABLE_KB = 350_000
SUPPORTED_OS = {("ubuntu", "22.04"), ("ubuntu", "24.04"), ("debian", "12")}

REMOTE_PROGRAM = r"""
set -u
host_ip="$1"; ports="${2//,/|}"
. /etc/os-release 2>/dev/null || true
echo "os_id=${ID:-unknown}"
echo "os_version=${VERSION_ID:-unknown}"
echo "arch=$(uname -m)"
echo "uid=$(id -u)"
echo "cpus=$(nproc)"
echo "mem_available_kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)"
echo "root_free_kb=$(df -Pk / | awk 'NR==2{print $4}')"
echo "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo unknown)"
echo "default_routes=$(ip -4 route show default 2>/dev/null | wc -l)"
echo "host_ip_assigned=$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -cx "$host_ip")"
if [ -d /run/systemd/system ]; then echo "systemd=1"; else echo "systemd=0"; fi
echo "udp_ports_busy=$(ss -H -lun 2>/dev/null | awk '{print $5}' | grep -cE ":($ports)$")"
"""


class CheckError(RuntimeError):
    """Fixed error codes only."""


def parse_output(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.isidentifier() and len(value) <= 64:
            values[key] = value
    return values


def _int(values: dict[str, str], key: str) -> int | None:
    value = values.get(key, "")
    return int(value) if value.isdecimal() else None


def evaluate(values: dict[str, str]) -> list[dict[str, object]]:
    """Pure function: facts in, ordered list of checks out. `required` ones gate provisioning."""
    mem = _int(values, "mem_available_kb")
    disk = _int(values, "root_free_kb")
    cpus = _int(values, "cpus")
    checks: list[tuple[str, bool, bool]] = [
        ("ssh_root", values.get("uid") == "0", True),
        (
            "os_supported",
            (values.get("os_id", ""), values.get("os_version", "")) in SUPPORTED_OS,
            True,
        ),
        ("arch_x86_64", values.get("arch") == "x86_64", True),
        ("systemd", values.get("systemd") == "1", True),
        # ADR-052: one default route, the public IPv4 assigned directly, forwarding already on.
        ("single_default_route", values.get("default_routes") == "1", True),
        ("public_ipv4_assigned_directly", values.get("host_ip_assigned") == "1", True),
        ("ip_forward_enabled", values.get("ip_forward") == "1", True),
        ("udp_ports_free", values.get("udp_ports_busy") == "0", True),
        ("memory_advisory", mem is not None and mem > MIN_MEM_AVAILABLE_KB, False),
        ("disk_advisory", disk is not None and disk > MIN_ROOT_FREE_KB, False),
        ("cpu_advisory", cpus is not None and cpus >= 1, False),
    ]
    return [{"check": name, "ok": ok, "required": required} for name, ok, required in checks]


def run_remote(node: BetaNode) -> dict[str, str]:
    ports = ",".join(str(port) for port in range(node.udp_ports[0], node.udp_ports[1] + 1))
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(node.ssh_key), "-p", str(node.ssh_port),
        f"{node.ssh_user}@{node.host}", "bash", "-s", "--", node.host, ports,
    ]  # fmt: skip
    try:
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell, fixed program on stdin.
            command, input=REMOTE_PROGRAM.encode("ascii"), capture_output=True, timeout=60,
            check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        raise CheckError("candidate_ssh_unavailable") from None
    if result.returncode != 0:
        raise CheckError("candidate_ssh_failed")
    return parse_output(result.stdout.decode("utf-8", "replace"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, help="node name from the inventory")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--receipt", type=Path, help="write the sanitized verdict to a NEW file")
    arguments = parser.parse_args(argv)
    try:
        inventory = load_inventory(arguments.inventory)
        node = inventory.get(arguments.node)
        if node is None:
            raise CheckError("candidate_not_in_inventory")
        checks = evaluate(run_remote(node))
    except (InventoryError, CheckError) as error:
        print(json.dumps({"verdict": "error", "error": str(error)}), file=sys.stderr)
        return 2
    required_ok = all(item["ok"] for item in checks if item["required"])
    report = {
        "node": node.name,
        "verdict": "ready" if required_ok else "not_ready",
        "checks": checks,
        "note": "preconditions only; not evidence of handshake, egress or usage attribution",
    }
    text = json.dumps(report, indent=2) + "\n"
    if arguments.receipt is not None:
        with arguments.receipt.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(text, end="")
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

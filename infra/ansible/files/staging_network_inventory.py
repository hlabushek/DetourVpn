"""I-010 read-only inventory of the host and two existing Amnezia namespaces.

Run on the approved staging host through the guarded Ansible playbook only.
No container executable, VPN config, key, environment or traffic is read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

CONTAINERS = ("amnezia-awg2", "amnezia-wireguard")
DOCKER = "/usr/bin/docker"
IP = "/usr/sbin/ip"
NSENTER = "/usr/bin/nsenter"
STATE_FORMAT = (
    '{"id":{{json .Id}},"pid":{{json .State.Pid}},'
    '"running":{{json .State.Running}},"started_at":{{json .State.StartedAt}},'
    '"networks":{{json .NetworkSettings.Networks}}}'
)
NETWORK_FORMAT = '{"name":{{json .Name}},"driver":{{json .Driver}},"ipam":{{json .IPAM.Config}}}'
ROUTE_FIELDS = ("dst", "gateway", "dev", "table", "type", "prefsrc", "scope")
RULE_FIELDS = ("priority", "src", "dst", "table", "action", "fwmark", "fwmask")
Run = Callable[[list[str]], str]


class InventoryError(Exception):
    """Fixed error code only; never include command stdout/stderr in errors."""


def run_command(argv: list[str]) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed tools/verbs; no shell
            argv, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InventoryError("probe_unavailable_or_timeout") from exc
    if result.returncode != 0:
        raise InventoryError("probe_command_failed")
    return result.stdout


def selected(data: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InventoryError("probe_invalid_output")
    return {field: data[field] for field in fields if field in data}


def state(run: Run, name: str) -> dict[str, Any]:
    value = json.loads(run([DOCKER, "container", "inspect", "--format", STATE_FORMAT, name]))
    if (
        not isinstance(value, dict)
        or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("id", "")))
        or type(value.get("pid")) is not int
        or value["pid"] <= 1
        or value.get("running") is not True
        or not isinstance(value.get("started_at"), str)
        or not value["started_at"]
        or not isinstance(value.get("networks"), dict)
    ):
        raise InventoryError("container_not_running_or_invalid_state")
    return value


def routes_and_rules(run: Run, prefix: list[str]) -> dict[str, Any]:
    result = {}
    for family in ("4", "6"):
        for kind, args, fields in (
            ("routes", ["route", "show", "table", "all"], ROUTE_FIELDS),
            ("rules", ["rule", "show"], RULE_FIELDS),
        ):
            data = json.loads(run([*prefix, IP, "-j", f"-{family}", *args]))
            if not isinstance(data, list):
                raise InventoryError("probe_invalid_output")
            # Preserve multipath destinations; report only network metadata.
            rows = []
            for item in data:
                row = selected(item, fields)
                if "nexthops" in item:
                    if not isinstance(item["nexthops"], list):
                        raise InventoryError("probe_invalid_output")
                    row["nexthops"] = [
                        selected(hop, ("gateway", "dev", "weight")) for hop in item["nexthops"]
                    ]
                rows.append(row)
            result[f"{kind}_v{family}"] = rows
    return result


def collect(run: Run = run_command) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "host": routes_and_rules(run, []),
        "docker_networks": [],
        "containers": [],
    }
    ids = run([DOCKER, "network", "ls", "--quiet", "--no-trunc"]).split()
    if not ids or len(ids) > 64 or not all(re.fullmatch(r"[a-f0-9]{64}", item) for item in ids):
        raise InventoryError("network_inventory_invalid_or_too_large")
    for network_id in ids:
        network = json.loads(
            run([DOCKER, "network", "inspect", "--format", NETWORK_FORMAT, network_id])
        )
        row = selected(network, ("name", "driver"))
        ipam = network.get("ipam") or []
        if not isinstance(ipam, list):
            raise InventoryError("probe_invalid_output")
        row["ipam"] = [selected(item, ("Subnet", "Gateway", "IPRange")) for item in ipam]
        report["docker_networks"].append(row)
    for name in CONTAINERS:
        before = state(run, name)
        networks = []
        for network_name, endpoint in before["networks"].items():
            endpoint_data = selected(
                endpoint,
                ("IPAddress", "IPPrefixLen", "Gateway", "GlobalIPv6Address", "IPv6PrefixLen"),
            )
            networks.append({"name": network_name, **endpoint_data})
        metadata = routes_and_rules(run, [NSENTER, f"--net=/proc/{before['pid']}/ns/net", "--"])
        after = state(run, name)
        if any(before[key] != after[key] for key in ("id", "pid", "started_at", "networks")):
            raise InventoryError("container_changed_during_probe")
        report["containers"].append({"name": name, "networks": networks, **metadata})
    return report


def main() -> int:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print('{"error":"requires_staging_sudo"}', file=sys.stderr)
        return 1
    try:
        report = collect()
    except (InventoryError, ValueError, TypeError, KeyError):
        # Do not serialize exception/command output: even unexpected failures
        # must not expose unselected metadata or partially collected results.
        print('{"error":"inventory_incomplete_do_not_choose_subnet"}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

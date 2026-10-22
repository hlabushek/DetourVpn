#!/usr/bin/python3 -I
"""Temporary staging-only control, a fixed command set; no remote shell API.

Installed root-owned and invoked through one exact, expiring sudoers grant.
The isolated interpreter and sanitized child environment exclude caller imports.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path("/opt/project-vpn/acceptance")
LEASE = Path("/run/project-vpn-i011-control/lease.json")
SUDOERS = Path("/etc/sudoers.d/project-vpn-i011-control")
STATE = Path("/run/project-vpn-i011")
INPUT = Path("/run/project-vpn-i011-input/public-key")
UNIT_FILE = Path("/etc/systemd/system/project-vpn-i011.service")
BOOT = Path("/proc/sys/kernel/random/boot_id")
UNIT = "project-vpn-i011.service"
HOST = "45.10.166.106"
ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LOG_LEVEL": "silent",
    "PYTHONDONTWRITEBYTECODE": "1",
}
ACTIONS = frozenset(
    {
        "preflight",
        "recover",
        "diagnose",
        "start",
        "status",
        "stop",
        "revoke",
        "key",
        "profile",
        "receipt",
        "expire",
    }
)
SOURCES = frozenset({"android_staging_window.py", "awg_local_smoke.py", "i011_control.py"})
RESTORED = frozenset(
    {"filter_nat", "ipv6_filter", "routes4", "routes6", "links", "namespaces", "containers"}
)
STAGES = frozenset(
    {
        "initialization",
        "environment",
        "trusted_paths",
        "tmpfs",
        "host_links",
        "host_default_route",
        "host_forwarding",
        "udp_listener",
        "host_firewall",
        "host_routes",
        "docker_ipam",
        "container_identity",
        "container_routes",
        "pinned_artifacts",
        "phone_public_key",
        "baseline_snapshot",
        "systemd_guard",
        "namespace_setup",
        "host_firewall_setup",
        "namespace_firewall_setup",
        "core_start",
        "ephemeral_config",
        "tunnel_interface_setup",
        "cleanup",
        "revoke_ownership",
        "revoke_peer_check",
        "revoke_peer_remove",
        "revoke_peer_verify",
    }
)


class ControlError(Exception):
    """Never serialize exception text or child output."""


def trusted(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ControlError
    if not path.is_file():
        raise ControlError


def command(argv: list[str], *, timeout: int = 30) -> str:
    result = subprocess.run(  # noqa: S603 - fixed tools/actions, never caller command text
        argv,
        capture_output=True,
        text=True,
        env=ENV,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise ControlError
    return result.stdout


def validate_lease(lease: dict[str, Any], uid: str | None, boot: str, now: int, ticks: int) -> None:
    if (
        lease.get("schema_version") != 1
        or lease.get("host") != HOST
        or lease.get("owner") != "vpn-deploy"
        or type(lease.get("owner_uid")) is not int
        or uid != str(lease["owner_uid"])
        or lease.get("boot_id") != boot
        or type(lease.get("expires_at")) is not int
        or now >= lease["expires_at"]
        or type(lease.get("expires_monotonic")) is not int
        or ticks >= lease["expires_monotonic"]
        or type(lease.get("issued_at")) is not int
        or now < lease["issued_at"]
        or not 0 < lease["expires_at"] - lease["issued_at"] <= 2700
        or type(lease.get("issued_monotonic")) is not int
        or ticks < lease["issued_monotonic"]
        or not 0 < lease["expires_monotonic"] - lease["issued_monotonic"] <= 2700
        or not isinstance(lease.get("code_sha256"), dict)
        or set(lease.get("code_sha256", {})) != SOURCES
    ):
        raise ControlError


def authorized() -> dict[str, Any]:
    trusted(LEASE)
    decoded = json.loads(LEASE.read_text(encoding="ascii"))
    if not isinstance(decoded, dict):
        raise ControlError
    lease: dict[str, Any] = decoded
    validate_lease(
        lease,
        os.environ.get("SUDO_UID"),
        BOOT.read_text().strip(),
        int(time.time()),
        int(time.monotonic()),
    )
    if os.environ.get("SUDO_USER") != "vpn-deploy":
        raise ControlError
    for name, expected in lease["code_sha256"].items():
        path = ROOT / name
        trusted(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ControlError
    trusted(UNIT_FILE)
    if hashlib.sha256(UNIT_FILE.read_bytes()).hexdigest() != lease.get("unit_sha256"):
        raise ControlError
    return lease


def service_state() -> str:
    value = command(
        ["/usr/bin/systemctl", "show", UNIT, "--property=ActiveState", "--value"]
    ).strip()
    if value not in {"active", "activating", "inactive", "deactivating", "failed"}:
        raise ControlError
    return value


def safe_events(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        safe: dict[str, Any] = {}
        for key in (
            "endpoint_ready",
            "egress_verified",
            "cleanup_complete",
            "cleanup_no_mutation_marker",
        ):
            if type(row.get(key)) is bool:
                safe[key] = row[key]
        if isinstance(row.get("restored"), dict):
            safe["restored"] = {
                key: value
                for key, value in row["restored"].items()
                if key in RESTORED and type(value) is bool
            }
        if row.get("error") == "staging_window_failed":
            safe["error"] = "staging_window_failed"
            safe["stage"] = row.get("stage") if row.get("stage") in STAGES else "unknown"
            safe["failure_code"] = (
                row.get("failure_code")
                if row.get("failure_code") in {"check_rejected", "probe_unavailable"}
                else "unknown"
            )
        if safe:
            events.append(safe)
    return events


def receipt() -> dict[str, Any]:
    invocation = command(
        ["/usr/bin/systemctl", "show", UNIT, "--property=InvocationID", "--value"]
    ).strip()
    if len(invocation) != 32 or any(char not in "0123456789abcdef" for char in invocation):
        raise ControlError
    text = command(
        [
            "/usr/bin/journalctl",
            f"_SYSTEMD_INVOCATION_ID={invocation}",
            "-n",
            "64",
            "--output=cat",
            "--no-pager",
        ]
    )
    return {"invocation_id": invocation, "events": safe_events(text)}


def set_public_key(text: str) -> None:
    if service_state() not in {"inactive", "failed"} or (STATE / "baseline.json").exists():
        raise ControlError
    value = text.strip()
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
        raise ControlError
    trusted(INPUT)
    temporary = INPUT.with_name("next-public-key")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(value + "\n")
    temporary.replace(INPUT)


def expire() -> None:
    # Safe even when invoked by the owner early, or after lease expiry/reboot.
    try:
        command(["/usr/bin/systemctl", "stop", UNIT], timeout=200)
    finally:
        SUDOERS.unlink(missing_ok=True)
        LEASE.unlink(missing_ok=True)


def main() -> int:
    try:
        if (
            getattr(os, "geteuid", lambda: -1)() != 0
            or len(sys.argv) != 2
            or sys.argv[1] not in ACTIONS
        ):
            raise ControlError
        action = sys.argv[1]
        trusted(Path(__file__).absolute())
        addresses = json.loads(command(["/usr/sbin/ip", "-j", "-4", "address", "show"]))
        if not any(
            addr.get("local") == HOST for row in addresses for addr in row.get("addr_info", [])
        ):
            raise ControlError
        if action == "expire":
            expire()
            print('{"control_grant_removed":true}')
            return 0
        lease = authorized()
        runner = ["/usr/bin/python3", str(ROOT / "android_staging_window.py")]
        result: dict[str, Any]
        if action in {"preflight", "revoke"}:
            probe = subprocess.run(  # noqa: S603 - fixed, hash-checked runner/action
                [*runner, action], capture_output=True, text=True, env=ENV, timeout=45, check=False
            )
            if probe.returncode:
                print(
                    json.dumps({"error": "i011_action_failed", "events": safe_events(probe.stdout)})
                )
                return 1
            row = json.loads(probe.stdout)
            expected = (
                {"preflight_passed": True, "network_changed": False}
                if action == "preflight"
                else {"peer_removed": True, "traffic_cutoff_verified": False}
            )
            if row != expected:
                raise ControlError
            result = expected
        elif action == "recover":
            output = command([*runner, "recover"])
            last = json.loads(output.splitlines()[-1])
            allowed = {
                "recovery_not_needed",
                "owned_resources_removed",
                "historical_snapshot_fully_verified",
                "new_baseline_required",
            }
            result = {
                "events": safe_events(output),
                "recovery": {
                    key: value
                    for key, value in last.items()
                    if key in allowed and type(value) is bool
                },
            }
        elif action == "diagnose":
            argv = [
                "/usr/bin/systemd-run",
                "--quiet",
                "--wait",
                "--pipe",
                "--collect",
                "--unit=project-vpn-i011-diagnostics",
                "--property=Type=exec",
                "--property=RuntimeMaxSec=45s",
                "--property=TimeoutStopSec=5s",
                "--property=KillMode=control-group",
                "--property=CapabilityBoundingSet=CAP_SYS_ADMIN CAP_NET_ADMIN CAP_NET_RAW",
                "--property=NoNewPrivileges=yes",
                "--property=PrivateTmp=yes",
                "--property=ProtectHome=yes",
                "--property=ProtectSystem=strict",
                "--property=ReadWritePaths=/run",
                "--property=LimitCORE=0",
                *runner,
                "preflight",
            ]
            # On failure, emit only the runner's sanitized JSON diagnostics.
            probe = subprocess.run(  # noqa: S603 - fixed command vector, no caller command text
                argv, capture_output=True, text=True, env=ENV, timeout=55, check=False
            )
            row = json.loads(probe.stdout)
            result = {
                "sandbox_exit": probe.returncode,
                "events": safe_events(probe.stdout),
                "preflight_passed": row.get("preflight_passed") is True,
            }
        elif action == "key":
            set_public_key(sys.stdin.read(128))
            result = {"phone_public_key_updated": True}
        elif action == "profile":
            if service_state() != "active":
                raise ControlError
            trusted(STATE / "ready.json")
            trusted(STATE / "phone.conf")
            data = (STATE / "phone.conf").read_bytes()
            if len(data) > 16384:
                raise ControlError
            # Secret channel: pipe directly to private client storage, never tool output/logs.
            sys.stdout.buffer.write(data)
            return 0
        elif action in {"start", "stop"}:
            command(["/usr/bin/systemctl", action, UNIT], timeout=200 if action == "stop" else 30)
            result = {"state": service_state()}
        elif action == "receipt":
            result = receipt()
        else:
            result = {
                "state": service_state(),
                "ready": (STATE / "ready.json").is_file(),
                "mutation_marker": (STATE / "baseline.json").is_file(),
                "lease_seconds_left": max(
                    0,
                    min(
                        lease["expires_at"] - int(time.time()),
                        lease["expires_monotonic"] - int(time.monotonic()),
                    ),
                ),
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ControlError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('{"error":"i011_control_refused"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

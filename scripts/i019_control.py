#!/usr/bin/python3 -I
"""I-019 only: expiring fixed actions on two explicitly approved staging hosts.

No caller-selected paths, programs, addresses or shell. profile is SECRET stdout:
capture in the private test harness, never print it or use it as evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path("/opt/project-vpn/acceptance")
STATE = Path("/run/project-vpn-i019-agent")
CONTROL = Path("/run/project-vpn-i019-control")
HOST_FILE = ROOT / "i019-host.json"
UNIT = "project-vpn-i019.service"
AGENT_UNIT = "project-vpn-i019-agent.service"
SUDOERS = Path("/etc/sudoers.d/project-vpn-i019-control")
SOURCES = frozenset(
    {
        "awg_local_smoke.py",
        "android_staging_window.py",
        "i011_control.py",
        "i012_control.py",
        "i012_agent.py",
        "i019_control.py",
        "i019-host.json",
    }
)
USER_ACTIONS = frozenset(
    {"status", "prepare", "start", "profile", "stop", "expire", "gate", "freeze-agent", "diagnose"}
)


def trusted(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError("untrusted_path")


def host() -> str:
    trusted(HOST_FILE)
    if HOST_FILE.stat().st_size > 128:
        raise ValueError("invalid_host")
    value = json.loads(HOST_FILE.read_text())
    if set(value) != {"host"} or value["host"] not in {"45.10.166.106", "85.209.132.205"}:
        raise ValueError("invalid_host")
    return str(value["host"])


def load(name: str, filename: str) -> Any:
    path = ROOT / filename
    trusted(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("missing_module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy(selected: str) -> Any:
    old = load("reviewed_i019_lease", "i011_control.py")
    old.HOST = selected
    old.LEASE = CONTROL / "lease.json"
    old.SUDOERS = SUDOERS
    old.STATE = Path("/run/project-vpn-i019")
    old.INPUT = Path("/run/project-vpn-i019-input/public-key")
    old.UNIT, old.UNIT_FILE = UNIT, Path("/etc/systemd/system") / UNIT
    old.SOURCES = SOURCES
    return old


def validate(old: Any) -> None:
    lease = old.authorized()
    path = Path("/etc/systemd/system") / AGENT_UNIT
    trusted(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != lease.get("agent_unit_sha256"):
        raise ValueError("agent_unit_changed")


def forwarding(old: Any, start: bool) -> bool:
    """Record before mutation. Runs only as internal root systemd hook."""
    marker = CONTROL / "forwarding.json"
    completed = CONTROL / "forwarding-restored.json"
    current = old.command(["/usr/sbin/sysctl", "-n", "net.ipv4.ip_forward"]).strip()
    if current not in {"0", "1"}:
        raise ValueError("invalid_forwarding")
    if start:
        trusted(CONTROL)
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"original": current}, stream)
        completed.unlink(missing_ok=True)
        if current == "0":
            old.command(["/usr/sbin/sysctl", "-q", "-w", "net.ipv4.ip_forward=1"])
        return True
    if not marker.exists():
        if not completed.exists():
            return True  # No start hook mutation occurred.
        trusted(completed)
        value = json.loads(completed.read_text())
        if set(value) != {"original"} or value["original"] not in {"0", "1"}:
            raise ValueError("invalid_restoration_record")
        return bool(current == value["original"])
    trusted(marker)
    value = json.loads(marker.read_text())
    if set(value) != {"original"} or value["original"] not in {"0", "1"}:
        raise ValueError("invalid_forwarding_marker")
    original = value["original"]
    if original == "0" and current == "1":
        old.command(["/usr/sbin/sysctl", "-q", "-w", "net.ipv4.ip_forward=0"])
    restored = old.command(["/usr/sbin/sysctl", "-n", "net.ipv4.ip_forward"]).strip() == original
    if restored:
        if completed.exists():
            trusted(completed)
            if json.loads(completed.read_text()) != {"original": original}:
                raise ValueError("restoration_record_conflict")
        else:
            fd = os.open(completed, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump({"original": original}, stream)
        marker.unlink()
    return bool(restored)


def lifecycle() -> Any:
    module = load("reviewed_i019_lifecycle", "i012_control.py")
    module.STATE, module.AGENT_UNIT, module.SUDOERS = STATE, AGENT_UNIT, SUDOERS
    return module


def profile(old: Any, selected: str) -> dict[str, Any]:
    value: dict[str, Any] = lifecycle().export_profile(old)
    value["endpoint"] = selected + ":51893"
    return value


def stop(old: Any) -> dict[str, Any]:
    agent_state = old.command(
        ["/usr/bin/systemctl", "show", AGENT_UNIT, "--property=ActiveState", "--value"]
    ).strip()
    if agent_state == "active":
        old.command(
            ["/usr/bin/systemctl", "kill", "--kill-whom=main", "--signal=SIGCONT", AGENT_UNIT]
        )
    stopped = True
    for unit in (AGENT_UNIT, UNIT):
        try:
            old.command(["/usr/bin/systemctl", "stop", unit], timeout=220)
        except Exception:
            stopped = False  # Still attempt the other stop and forwarding restoration.
    restored = forwarding(old, False)
    invocation = old.command(
        ["/usr/bin/systemctl", "show", UNIT, "--property=InvocationID", "--value"]
    ).strip()
    receipt = old.receipt() if re.fullmatch(r"[0-9a-f]{32}", invocation) else {"events": []}
    events = receipt["events"]
    complete = any(
        row.get("cleanup_complete") is True
        and len(row.get("restored", {})) == 7
        and all(row["restored"].values())
        for row in events
    )
    owned_absent = not any(
        path.exists()
        for path in (
            old.STATE / "baseline.json",
            Path("/sys/class/net/v19host"),
            Path("/run/netns/vpn-i019"),
            Path("/run/amneziawg/v19awg.sock"),
        )
    )
    no_mutation = not invocation or any(
        row.get("cleanup_no_mutation_marker") is True for row in events
    )
    return {
        "cleanup_complete": stopped and restored and owned_absent and (complete or no_mutation),
        "forwarding_restored": restored,
        "cleanup_receipt": receipt,
        "owned_resources_absent": owned_absent,
    }


def run_agent() -> int:
    agent = load("reviewed_i019_agent", "i012_agent.py")
    agent.ROOT = STATE
    agent.ENDPOINT_READY = Path("/run/project-vpn-i019/ready.json")
    agent.ENDPOINT_CGROUP = "0::/system.slice/" + UNIT
    agent.NS, agent.INTERFACE, agent.PORT, agent.TABLE = "vpn-i019", "v19awg", 51893, "vpn_i019"
    try:
        return int(agent.main())
    except Exception:
        # Only fixed phase vocabulary from the existing agent; never exception text.
        print(json.dumps({"error": "node_agent_failed", "stage": agent.AGENT_STAGE}), flush=True)
        return 1


def main() -> int:
    if getattr(os, "geteuid", lambda: -1)() != 0 or len(sys.argv) != 2:
        raise ValueError("control_refused")
    action = sys.argv[1]
    internal = {"agent", "forwarding-start", "forwarding-stop"}
    if action not in USER_ACTIONS | internal:
        raise ValueError("control_refused")
    trusted(Path(__file__).absolute())
    selected = host()
    old = legacy(selected)
    addresses = json.loads(old.command(["/usr/sbin/ip", "-j", "-4", "address", "show"]))
    if not any(a.get("local") == selected for row in addresses for a in row.get("addr_info", [])):
        raise ValueError("wrong_host")
    if action in internal:
        if os.environ.get("SUDO_USER"):
            raise ValueError("internal_action")
        if action == "agent":
            return run_agent()
        ok = forwarding(old, action == "forwarding-start")
        print(
            json.dumps(
                {"forwarding_restored" if action.endswith("stop") else "forwarding_ready": ok}
            )
        )
        return 0 if ok else 1
    if action == "expire":
        # Root expiry works even after grant deadline. Sudo caller still requires lease.
        if os.environ.get("SUDO_USER"):
            validate(old)
        try:
            result = stop(old)
        finally:
            try:
                old.command(["/usr/bin/systemctl", "stop", "project-vpn-i019-expiry.timer"])
            finally:
                SUDOERS.unlink(missing_ok=True)
                old.LEASE.unlink(missing_ok=True)
                for name in ("config.json", "journal.json", "lock"):
                    (STATE / name).unlink(missing_ok=True)
        print(json.dumps(result | {"control_grant_removed": True}))
        return 0
    validate(old)
    if action == "diagnose":
        try:
            receipt = old.receipt()
        except Exception:
            receipt = {"events": []}
        print(
            json.dumps(
                {
                    "endpoint_state": old.service_state(),
                    "agent_failure_stage": lifecycle().agent_failure(old),
                    "receipt": receipt,
                }
            )
        )
    elif action == "status":
        agent_state = old.command(
            ["/usr/bin/systemctl", "show", AGENT_UNIT, "--property=ActiveState", "--value"]
        ).strip()
        print(
            json.dumps(
                {
                    "endpoint_active": old.service_state() == "active",
                    "agent_active": agent_state == "active",
                }
            )
        )
    elif action == "prepare":
        if (
            old.service_state() not in {"inactive", "failed"}
            or (old.STATE / "baseline.json").exists()
        ):
            raise ValueError("window_already_active")
        agent_state = old.command(
            ["/usr/bin/systemctl", "show", AGENT_UNIT, "--property=ActiveState", "--value"]
        ).strip()
        if agent_state not in {"inactive", "failed"}:
            raise ValueError("agent_active")
        raw = sys.stdin.read(4097)
        if len(raw) > 4096:
            raise ValueError("input_too_large")
        cfg = json.loads(raw)
        if set(cfg) != {"node_id", "runtime_id", "credential", "bootstrap_public_key"}:
            raise ValueError("invalid_config")
        UUID(cfg["node_id"])
        UUID(cfg["runtime_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", cfg["credential"]):
            raise ValueError("invalid_credential")
        old.set_public_key(cfg["bootstrap_public_key"])
        trusted(STATE)
        for name in ("config.json", "journal.json", "lock"):
            (STATE / name).unlink(missing_ok=True)
        fd = os.open(STATE / "config.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({key: cfg[key] for key in ("node_id", "runtime_id", "credential")}, stream)
        print('{"prepared":true}')
    elif action == "start":
        trusted(STATE / "config.json")
        cfg = json.loads((STATE / "config.json").read_text())
        if "server_public_key" in cfg or old.service_state() not in {"inactive", "failed"}:
            raise ValueError("window_already_used")
        # Full privileged, read-only baseline BEFORE even temporary forwarding.
        old.command(
            [
                "/usr/bin/python3",
                str(ROOT / "android_staging_window.py"),
                "preflight",
                "--i019-host",
                selected,
            ]
        )
        old.command(["/usr/bin/systemctl", "start", UNIT])
        for _ in range(100):
            if (old.STATE / "ready.json").exists():
                break
            if old.service_state() not in {"activating", "active"}:
                raise ValueError("endpoint_failed")
            time.sleep(0.2)
        else:
            raise ValueError("endpoint_timeout")
        cfg["server_public_key"] = profile(old, selected)["server_public_key"]
        (STATE / "config.json").write_text(json.dumps(cfg))
        old.command(["/usr/bin/systemctl", "start", AGENT_UNIT])
        print('{"endpoint_started":true,"agent_started":true}')
    elif action == "profile":
        print(json.dumps(profile(old, selected)))
    elif action == "stop":
        print(json.dumps(stop(old)))
    elif action == "freeze-agent":
        old.command(
            ["/usr/bin/systemctl", "kill", "--kill-whom=main", "--signal=SIGSTOP", AGENT_UNIT]
        )
        print('{"agent_frozen":true}')
    elif action == "gate":
        # Fixed read-only kernel probe. Does not expose peers, counters or key material.
        agent = load("reviewed_i019_gate", "i012_agent.py")
        agent.ENDPOINT_READY = Path("/run/project-vpn-i019/ready.json")
        agent.ENDPOINT_CGROUP = "0::/system.slice/" + UNIT
        fd = agent.open_endpoint_namespace()
        try:
            raw = old.command(
                [
                    "/usr/bin/nsenter",
                    f"--net=/proc/{os.getpid()}/fd/{fd}",
                    "--",
                    "/usr/sbin/nft",
                    "-j",
                    "list",
                    "set",
                    "inet",
                    "vpn_i019",
                    "live",
                ]
            )
        finally:
            os.close(fd)
        tables = json.loads(raw)
        elements = [row["set"].get("elem", []) for row in tables["nftables"] if "set" in row]
        print(json.dumps({"kernel_gate_empty": elements == [[]]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('{"error":"i019_control_refused"}')
        raise SystemExit(1) from None

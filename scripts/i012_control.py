#!/usr/bin/python3 -I
"""Expiring staging-only control; fixed actions, no passwords or general shell."""

from __future__ import annotations

import configparser
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
STATE = Path("/run/project-vpn-i012-agent")
AGENT_UNIT = "project-vpn-i012-agent.service"
SUDOERS = Path("/etc/sudoers.d/project-vpn-i012-control")
EXPECTED = {"i012_control.py", "i012_agent.py", "project-vpn-i012-agent.service"}


def trusted_directory(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise ValueError("untrusted_directory")


def legacy() -> Any:
    path = ROOT / "i011_control.py"
    # Check ownership/parents before importing any code through the isolated interpreter.
    import stat

    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError("untrusted_control")
    spec = importlib.util.spec_from_file_location("reviewed_i011_control", path)
    if spec is None or spec.loader is None:
        raise ValueError("missing_control")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(old: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = old.authorized()
    hashes = metadata.get("i012_sha256", {})
    if set(hashes) != EXPECTED:
        raise ValueError("unapproved_code")
    for name, digest in hashes.items():
        path = (Path("/etc/systemd/system") if name.endswith(".service") else ROOT) / name
        old.trusted(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("control_code_changed")
    return metadata


def stop(old: Any) -> dict[str, Any]:
    old.command(["/usr/bin/systemctl", "stop", AGENT_UNIT], timeout=30)
    old.command(["/usr/bin/systemctl", "stop", old.UNIT], timeout=220)
    receipt: dict[str, Any] = old.receipt()
    events = receipt["events"]
    clean = any(
        row.get("cleanup_complete") is True
        and len(row.get("restored", {})) == 7
        and all(row["restored"].values())
        for row in events
    )
    # Full baseline comparison is emitted by the existing cleanup helper, not inferred
    # from systemctl returning zero. Receipt is already sanitized by the I-011 parser.
    return {"cleanup_receipt": receipt, "cleanup_complete": clean}


def export_profile(old: Any) -> dict[str, Any]:
    old.trusted(STATE / "config.json")
    cfg = json.loads((STATE / "config.json").read_text())
    source = old.STATE / "phone.conf"
    old.trusted(source)
    parser = configparser.ConfigParser()
    parser.read_string(source.read_text())
    server_public = parser["Peer"]["PublicKey"]
    protection = parser["Interface"]["HeaderProtectionKey"]
    if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", server_public) or not re.fullmatch(
        r"[A-Za-z0-9+/]{43}=", protection
    ):
        raise ValueError("invalid_profile")
    return {
        "node_id": cfg["node_id"],
        "runtime_id": cfg["runtime_id"],
        "server_public_key": server_public,
        "header_protection_key": protection,
        "endpoint": "45.10.166.106:51892",
    }


def agent_failure(old: Any) -> str | None:
    invocation = old.command(
        ["/usr/bin/systemctl", "show", AGENT_UNIT, "--property=InvocationID", "--value"]
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{32}", invocation):
        return None
    output = old.command(
        [
            "/usr/bin/journalctl",
            f"_SYSTEMD_INVOCATION_ID={invocation}",
            "-n",
            "8",
            "--output=cat",
            "--no-pager",
        ]
    )
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(record, dict)
            and record.get("error") == "node_agent_failed"
            and record.get("stage")
            in {
                "initialize",
                "namespace_identity",
                "validate_native",
                "initialize_gate",
                "read_inventory",
                "remove_bootstrap_peer",
                "persist_journal",
                "running",
            }
        ):
            return str(record["stage"])
    return None


def main() -> int:
    if getattr(os, "geteuid", lambda: -1)() != 0 or len(sys.argv) != 2:
        raise ValueError("control_refused")
    action = sys.argv[1]
    if action not in {"prepare", "start", "profile", "status", "stop", "restart-agent", "expire"}:
        raise ValueError("control_refused")
    old = legacy()
    old.trusted(Path(__file__).absolute())
    addresses = json.loads(old.command(["/usr/sbin/ip", "-j", "-4", "address", "show"]))
    if not any(
        addr.get("local") == old.HOST for row in addresses for addr in row.get("addr_info", [])
    ):
        raise ValueError("wrong_host")
    if action == "expire":
        try:
            result = stop(old)
        finally:
            # An earlier manual finish must not leave a timer that later interferes
            # with a newly approved window or deletes another fresh lease.
            try:
                old.command(["/usr/bin/systemctl", "stop", "project-vpn-i012-control-expiry.timer"])
            finally:
                SUDOERS.unlink(missing_ok=True)
                old.LEASE.unlink(missing_ok=True)
                for name in ("config.json", "journal.json", "lock"):
                    (STATE / name).unlink(missing_ok=True)
        print(json.dumps(result | {"control_grant_removed": True}))
        return 0
    validate(old)
    if action == "prepare":
        if (
            old.service_state() not in {"inactive", "failed"}
            or (old.STATE / "baseline.json").exists()
        ):
            raise ValueError("window_already_active")
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
        trusted_directory(STATE)
        path = STATE / "config.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            json.dump({key: cfg[key] for key in ("node_id", "runtime_id", "credential")}, output)
        old.set_public_key(cfg["bootstrap_public_key"])
        print('{"prepared":true}')
        return 0
    if action == "start":
        old.trusted(STATE / "config.json")
        if "server_public_key" in json.loads((STATE / "config.json").read_text()):
            raise ValueError("window_already_used")
        if old.service_state() not in {"inactive", "failed"}:
            raise ValueError("window_already_active")
        old.command(["/usr/bin/systemctl", "start", old.UNIT])
        for _ in range(80):
            if (old.STATE / "ready.json").exists():
                break
            if old.service_state() not in {"activating", "active"}:
                raise ValueError("endpoint_failed")
            time.sleep(0.25)
        else:
            raise ValueError("endpoint_timeout")
        selected = export_profile(old)
        path = STATE / "config.json"
        cfg = json.loads(path.read_text())
        cfg["server_public_key"] = selected["server_public_key"]
        path.write_text(json.dumps(cfg), encoding="ascii")
        old.command(["/usr/bin/systemctl", "start", AGENT_UNIT])
        print('{"endpoint_started":true,"agent_started":true}')
        return 0
    if action == "profile":
        # SECRET CHANNEL ONLY. Caller must capture stdout directly into private storage.
        print(json.dumps(export_profile(old)))
        return 0
    if action == "restart-agent":
        old.command(["/usr/bin/systemctl", "restart", AGENT_UNIT])
        print('{"agent_restarted":true}')
        return 0
    if action == "stop":
        print(json.dumps(stop(old)))
        return 0
    print(
        json.dumps(
            {
                "endpoint_active": old.service_state() == "active",
                "agent_active": old.command(
                    ["/usr/bin/systemctl", "show", AGENT_UNIT, "--property=ActiveState", "--value"]
                ).strip()
                == "active",
                "agent_failure_stage": agent_failure(old),
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('{"error":"i012_control_refused"}')
        raise SystemExit(1) from None

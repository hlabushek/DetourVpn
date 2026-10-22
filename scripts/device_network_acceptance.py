"""I-011 debug phone orchestration. Never print or persist the staging profile.

Explicit live-network tests only. ADB target-private files are not a product API.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe"
PACKAGE = "org.example.vpnpoc"
CONTROL = "/opt/project-vpn/acceptance/i011_control.py"


class AcceptanceError(Exception):
    """Fixed error labels, no command response or configuration in exceptions."""


def execute(argv: list[str], data: bytes | None = None, *, timeout: int = 30) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed tools/actions; no host shell
        argv,
        input=data,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise AcceptanceError("command_failed")
    return result.stdout


def adb(*args: str, data: bytes | None = None) -> bytes:
    return execute([str(ADB), *args], data)


def server(action: str, data: bytes | None = None) -> bytes:
    if action not in {"status", "key", "profile"}:
        raise AcceptanceError("unsupported_control_action")
    return execute(
        [
            "wsl",
            "-d",
            "kali-linux",
            "--",
            "ssh",
            "-i",
            "/home/ad/.ssh/vpn_staging_20261019",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "vpn-deploy@45.10.166.106",
            f"sudo -n {CONTROL} {action}",
        ],
        data,
    )


def status() -> dict[str, Any]:
    row = json.loads(adb("exec-out", "run-as", PACKAGE, "cat", "files/i011-result.json"))
    if not isinstance(row, dict) or not isinstance(row.get("stage"), str):
        raise AcceptanceError("invalid_device_result")
    # All other values must be booleans or a fixed internal failed_at label.
    return {
        key: value
        for key, value in row.items()
        if type(value) is bool or key in {"stage", "failed_at"} and isinstance(value, str)
    }


def write_command(action: str) -> None:
    if action not in {"connect", "probe", "dns", "disconnect", "finish"}:
        raise AcceptanceError("unsupported_device_action")
    adb("shell", "run-as", PACKAGE, "rm", "-f", "files/i011-result.json")
    adb(
        "shell",
        "-T",
        "run-as org.example.vpnpoc sh -c 'cat > files/i011-command-next "
        "&& mv files/i011-command-next files/i011-command'",
        data=action.encode("ascii"),
    )


def wait_result(expected: str, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            row = status()
        except (AcceptanceError, ValueError, KeyError):
            time.sleep(0.5)
            continue
        if row["stage"] in {expected, "failed"}:
            return row
        time.sleep(0.5)
    raise AcceptanceError("device_result_timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("status", "key", "connect", "probe", "dns", "disconnect", "finish")
    )
    args = parser.parse_args()
    try:
        if args.action == "status":
            result = status()
        elif args.action == "key":
            if status()["stage"] != "waiting_profile":
                raise AcceptanceError("device_not_waiting_for_profile")
            value = adb("exec-out", "run-as", PACKAGE, "cat", "files/i011-public-key").strip()
            raw = base64.b64decode(value, validate=True)
            if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw) != value:
                raise AcceptanceError("invalid_public_key")
            result = json.loads(server("key", value + b"\n"))
        else:
            if args.action == "connect":
                remote = json.loads(server("status"))
                if remote.get("state") != "active" or remote.get("ready") is not True:
                    raise AcceptanceError("server_not_ready")
                profile = server("profile")
                if (
                    not profile.startswith(b"[Interface]\n")
                    or len(profile) > 16384
                    or b"PrivateKey" in profile
                ):
                    raise AcceptanceError("invalid_profile_channel")
                adb(
                    "shell",
                    "-T",
                    "run-as org.example.vpnpoc sh -c 'cat > files/i011-profile-next.conf "
                    "&& mv files/i011-profile-next.conf files/i011-profile.conf'",
                    data=profile,
                )
                del profile
            write_command(args.action)
            expected = {
                "connect": "connected_probe_complete",
                "probe": "probe_complete",
                "dns": "dns_probe_complete",
                "disconnect": "disconnected_probe_complete",
                "finish": "complete",
            }[args.action]
            result = wait_result(expected, 90)
        print(json.dumps(result, sort_keys=True))
        return 1 if result.get("stage") == "failed" else 0
    except (AcceptanceError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('{"error":"device_acceptance_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

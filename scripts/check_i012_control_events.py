"""Physical Android control IPC preflight only. No VPN, API, SSH or sudo operations."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe"
PACKAGE = "org.example.vpnpoc"


def adb(*args: str) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed SDK executable and fixed device actions
        [str(ADB), *args], capture_output=True, timeout=30, check=False
    )
    if result.returncode:
        raise RuntimeError("control_preflight_adb_failed")
    return result.stdout


def main() -> int:
    if sum(line.endswith(b"\tdevice") for line in adb("devices").splitlines()) != 1:
        raise RuntimeError("control_preflight_requires_one_ready_phone")
    process: subprocess.Popen[bytes] | None = None
    try:
        adb("install", "-r", str(ROOT / "apps/android/app/build/outputs/apk/debug/app-debug.apk"))
        adb(
            "install",
            "-r",
            str(
                ROOT
                / "apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
            ),
        )
        adb("shell", "am", "force-stop", PACKAGE)
        adb(
            "shell",
            "run-as",
            PACKAGE,
            "rm",
            "-f",
            "files/i012-result.json",
            "files/i012-local-control-ack",
        )
        process = subprocess.Popen(  # noqa: S603 - fixed test APK, captures all raw output
            [
                str(ADB),
                "shell",
                "am",
                "instrument",
                "-w",
                "-e",
                "controlEvent",
                "true",
                PACKAGE + ".test/org.example.vpnpoc.PocDeviceTests",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # Deliberately miss the transient phase; only the persistent event can trigger ACK.
        time.sleep(1)
        deadline = time.monotonic() + 15
        delivered = False
        while process.poll() is None and time.monotonic() < deadline:
            try:
                state = json.loads(
                    adb("exec-out", "run-as", PACKAGE, "cat", "files/i012-result.json")
                )
            except (ValueError, RuntimeError):
                time.sleep(0.1)
                continue
            if state.get("stage") == "waiting_revoke_ack" and state.get("control_events") == [
                "restart_agent"
            ]:
                adb("shell", "run-as", PACKAGE, "touch", "files/i012-local-control-ack")
                delivered = True
                break
            time.sleep(0.1)
        output, _ = process.communicate(timeout=15)
        facts = {}
        for line in output.decode("utf-8", errors="replace").splitlines():
            prefix = "INSTRUMENTATION_RESULT: lifecycle_facts="
            if line.startswith(prefix):
                facts = json.loads(line[len(prefix) :])
        if (
            not delivered
            or facts.get("stage") != "complete"
            or facts.get("durable_control_event_acknowledged") is not True
        ):
            raise RuntimeError("control_event_preflight_failed")
        print(
            "I-012 local control IPC: delayed event delivered and acknowledged; no VPS operations."
        )
        return 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        adb("shell", "am", "force-stop", PACKAGE)
        adb("shell", "run-as", PACKAGE, "rm", "-f", "files/i012-local-control-ack")
        adb("uninstall", PACKAGE + ".test")


if __name__ == "__main__":
    raise SystemExit(main())

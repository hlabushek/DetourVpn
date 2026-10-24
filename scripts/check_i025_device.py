"""Physical I-025 native checks on a fresh disposable install; no node or WAN changes."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org.example.vpnclient"
TEST_PACKAGE = PACKAGE + ".test"
PHASES = (
    "seed",
    "restart",
    "logout",
    "verify_logout",
    "corrupt_session",
    "verify_session_corruption",
    "corrupt_device",
    "verify_device_corruption",
    "restored_identity",
    "permission",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--app-apk", type=Path, required=True)
    parser.add_argument("--test-apk", type=Path, required=True)
    args = parser.parse_args()

    def adb(*parts: str, timeout: int = 90) -> str:
        result = subprocess.run(  # noqa: S603 - fixed local ADB binary and explicit args.
            [str(args.adb), "-s", args.serial, *parts],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("i025_adb_operation_failed")  # Never echo device/process output.
        return result.stdout.decode("utf-8", "replace")

    def installed(package: str) -> bool:
        return "package:" + package in adb("shell", "pm", "list", "packages", package).splitlines()

    report: dict[str, object] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "scope": "physical Android native storage/lifecycle/permission only",
        "vpn_dataplane_acceptance": False,
        "api_auth_session_acceptance": False,
        "remote_changes": False,
        "test_session": "TEST ONLY noncredential marker, no real auth",
        "facts": {},
        "passed": False,
        "phases": {},
    }
    # No uninstall/update/clear of an existing account is allowed by this runner.
    if installed(PACKAGE) or installed(TEST_PACKAGE):
        raise RuntimeError("i025_requires_absent_test_target_preserve_existing_app_data")
    if adb("shell", "getprop", "ro.kernel.qemu").strip() == "1":
        raise RuntimeError("i025_physical_phone_required")
    if int(adb("shell", "getprop", "ro.build.version.sdk").strip()) < 33:
        raise RuntimeError("i025_instrumentation_runner_requires_api_33")
    report["android_api"] = int(adb("shell", "getprop", "ro.build.version.sdk").strip())
    poc_present = installed("org.example.vpnpoc")
    dns_before = adb("shell", "settings", "get", "global", "private_dns_mode").strip()
    ordinary = False
    test = False
    stage = "install"
    try:
        adb("install", "-r", str(args.app_apk))
        ordinary = True
        adb("install", "-r", "-t", str(args.test_apk))
        test = True
        facts: dict[str, bool] = {}
        phases: dict[str, dict[str, object]] = {}
        for phase in PHASES:
            stage = phase
            adb("shell", "am", "force-stop", PACKAGE)
            output = adb(
                "shell",
                "am",
                "instrument",
                "-w",
                "-r",
                "-e",
                "phase",
                phase,
                TEST_PACKAGE + "/org.example.vpnpoc.I025DeviceTests",
            )
            values = {}
            for line in output.splitlines():
                if line.startswith("INSTRUMENTATION_RESULT: ") and "=" in line:
                    key, value = line.removeprefix("INSTRUMENTATION_RESULT: ").split("=", 1)
                    values[key] = value
            passed = values.get("passed") == "true"
            phases[phase] = {"passed": passed}
            if not passed:
                failed_at = values.get("failed_at", phase)
                phases[phase]["failed_at"] = (
                    failed_at
                    if failed_at in PHASES + ("startup", "permission_dialog")
                    else "native_check"
                )
                report["phases"] = phases
                raise RuntimeError("i025_native_phase_failed")
            phases[phase]["process_id"] = int(values["process_id"])
            for key, value in values.items():
                if (
                    key not in {"passed", "tunnel_or_network_acceptance", "process_id"}
                    and value == "true"
                ):
                    facts[key] = True
            report["facts"] = facts
            report["phases"] = phases
            print("I-025 physical phase " + phase + ": passed", flush=True)
        if (
            phases["seed"]["process_id"] == phases["restart"]["process_id"]
            or phases["logout"]["process_id"] == phases["verify_logout"]["process_id"]
        ):
            raise RuntimeError("i025_process_restart_not_confirmed")
        facts["distinct_android_processes_verified"] = True
        report["passed"] = True
    except (RuntimeError, ValueError, KeyError, AssertionError, subprocess.SubprocessError):
        report["failed_at"] = stage
    finally:
        cleaned = True
        try:
            if test:
                adb("uninstall", TEST_PACKAGE)
            if ordinary:
                adb("shell", "am", "force-stop", PACKAGE)
                adb("uninstall", PACKAGE)  # Created by this run; no prior account/data.
                adb("install", "-r", str(args.app_apk))
            cleaned = not installed(TEST_PACKAGE) and installed(PACKAGE)
            cleaned &= installed("org.example.vpnpoc") == poc_present
            cleaned &= (
                adb("shell", "settings", "get", "global", "private_dns_mode").strip() == dns_before
            )
        except (RuntimeError, subprocess.SubprocessError):
            cleaned = False
        report["ordinary_app_restored_test_installation_removed"] = cleaned
        report["passed"] = bool(report["passed"]) and cleaned
        (ROOT / "docs/evidence/i-025-native-android.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

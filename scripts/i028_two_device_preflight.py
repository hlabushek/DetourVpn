"""Read-only local readiness check for the I-028 simultaneous-device run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def _run(adb: str, serial: str, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed adb executable and argument list.
        [adb, "-s", serial, *args],
        capture_output=True,
        check=False,
        timeout=30,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError("adb_read_only_check_failed")
    return result.stdout.strip()


def inspect_devices(
    adb: str,
    ready: dict[str, object],
    run: Callable[..., str] = _run,
) -> dict[str, object]:
    listed = subprocess.run(  # noqa: S603 - fixed adb devices inventory command.
        [adb, "devices", "-l"],
        capture_output=True,
        check=False,
        timeout=30,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if listed.returncode:
        raise RuntimeError("adb_inventory_failed")
    attached = []
    for line in listed.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device" and not fields[0].startswith("emulator-"):
            attached.append(fields[0])
    if len(attached) != 2 or len(set(attached)) != 2:
        raise RuntimeError("exactly_two_physical_android_devices_required")

    base_apk = Path(str(ready.get("probe_base_apk", "")))
    test_apk = Path(str(ready.get("probe_apk", "")))
    if not base_apk.is_file() or not test_apk.is_file():
        raise RuntimeError("source_bound_probe_apk_pair_missing")
    expected_hashes = {
        "probe_base_sha256": base_apk,
        "probe_test_sha256": test_apk,
    }
    for field, artifact in expected_hashes.items():
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if ready.get(field) != actual:
            raise RuntimeError("source_bound_probe_apk_hash_mismatch")

    devices: list[dict[str, object]] = []
    forbidden_packages = (
        "org.example.vpnclient.i028",
        "org.example.vpnclient.i028.test",
        "org.example.vpnpoc.test",
    )
    for serial in attached:
        model = run(adb, serial, "shell", "getprop", "ro.product.model")
        android = run(adb, serial, "shell", "getprop", "ro.build.version.release")
        if not model or not android:
            raise RuntimeError("physical_device_identity_unavailable")
        packages = run(adb, serial, "shell", "pm", "list", "packages")
        if any(f"package:{package}" in packages.splitlines() for package in forbidden_packages):
            raise RuntimeError("preexisting_i028_test_package_preserved")
        always_on = run(adb, serial, "shell", "settings", "get", "secure", "always_on_vpn_app")
        lockdown = run(
            adb,
            serial,
            "shell",
            "settings",
            "get",
            "secure",
            "always_on_vpn_lockdown",
        )
        if always_on not in {"null", ""} or lockdown not in {"null", "0", "false", ""}:
            raise RuntimeError("preexisting_always_on_policy_preserved")
        poc_installed = "package:org.example.vpnpoc" in packages.splitlines()
        devices.append(
            {
                "model": model,
                "android_release": android,
                "probe_base_present": poc_installed,
                "probe_base_install_needed": not poc_installed,
                "isolated_i028_package_absent": True,
                "probe_test_package_absent": True,
                "always_on_vpn_unconfigured": True,
            }
        )
    return {
        "passed": True,
        "scope": "read-only local two-physical-device preflight; no install or staging changes",
        "device_count": len(devices),
        "devices": devices,
        "probe_artifacts_hash_verified": True,
        "vpn_dataplane_acceptance": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument(
        "--adb",
        type=Path,
        default=Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe",
    )
    args = parser.parse_args()
    try:
        ready = json.loads(args.ready.read_text(encoding="utf-8"))
        if not isinstance(ready, dict):
            raise RuntimeError("runner_readiness_invalid")
        result = inspect_devices(str(args.adb), ready)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"passed": False, "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from scripts.i028_two_device_preflight import inspect_devices


def _inputs(tmp_path: Path) -> dict[str, object]:
    base = tmp_path / "probe.apk"
    test = tmp_path / "probe-test.apk"
    base.write_bytes(b"base-apk")
    test.write_bytes(b"test-apk")
    return {
        "probe_base_apk": str(base),
        "probe_apk": str(test),
        "probe_base_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
        "probe_test_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
    }


def _adb_list(monkeypatch: pytest.MonkeyPatch, count: int = 2) -> None:
    lines = [
        f"serial-{i} device product:p model:Android device:android transport_id:{i}"
        for i in range(count)
    ]
    lines.append("untrusted unauthorized product:p model:Android device:android")

    def listed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            args[0], 0, "List of devices attached\n" + "\n".join(lines) + "\n", ""
        )

    monkeypatch.setattr("scripts.i028_two_device_preflight.subprocess.run", listed)


def test_two_device_preflight_is_read_only_and_redacts_serials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adb_list(monkeypatch)

    def run(_adb: str, _serial: str, *args: str) -> str:
        if args[-1] == "ro.product.model":
            return "Android test device"
        if args[-1] == "ro.build.version.release":
            return "15"
        if args[2:4] == ("list", "packages"):
            return ""
        if args[-1] == "always_on_vpn_app":
            return "null"
        if args[-1] == "always_on_vpn_lockdown":
            return "0"
        raise AssertionError(args)

    result = inspect_devices("adb", _inputs(tmp_path), run)
    assert result["passed"] is True
    assert result["device_count"] == 2
    assert result["vpn_dataplane_acceptance"] is False
    assert "serial-0" not in str(result) and "serial-1" not in str(result)
    assert all(device["probe_base_install_needed"] for device in result["devices"])


def test_two_device_preflight_refuses_wrong_device_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adb_list(monkeypatch, count=1)
    with pytest.raises(RuntimeError, match="exactly_two_physical_android_devices_required"):
        inspect_devices("adb", _inputs(tmp_path), lambda *_args: "unused")


@pytest.mark.parametrize(
    ("packages", "always_on", "lockdown", "error"),
    [
        ("package:org.example.vpnpoc.test", "null", "0", "preexisting_i028_test_package_preserved"),
        ("", "com.example.other", "0", "preexisting_always_on_policy_preserved"),
        ("", "null", "1", "preexisting_always_on_policy_preserved"),
    ],
)
def test_two_device_preflight_preserves_existing_test_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    packages: str,
    always_on: str,
    lockdown: str,
    error: str,
) -> None:
    _adb_list(monkeypatch)

    def run(_adb: str, _serial: str, *args: str) -> str:
        if args[-1] == "ro.product.model":
            return "Android test device"
        if args[-1] == "ro.build.version.release":
            return "15"
        if args[2:4] == ("list", "packages"):
            return packages
        if args[-1] == "always_on_vpn_app":
            return always_on
        if args[-1] == "always_on_vpn_lockdown":
            return lockdown
        raise AssertionError(args)

    with pytest.raises(RuntimeError, match=error):
        inspect_devices("adb", _inputs(tmp_path), run)


def test_two_device_preflight_refuses_changed_apk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adb_list(monkeypatch)
    ready = _inputs(tmp_path)
    Path(str(ready["probe_apk"])).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="source_bound_probe_apk_hash_mismatch"):
        inspect_devices("adb", ready, lambda *_args: "unused")

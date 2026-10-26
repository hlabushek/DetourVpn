"""Synthetic ADB fault tests only; never physical lifecycle acceptance."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "i028_device", ROOT / "scripts/i028_device_scenarios.py"
)
assert spec is not None and spec.loader is not None
device: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)


class Adb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_unforce = False
        self.fail_observe = False
        self.override = False
        self.idle = "ACTIVE"
        self.wifi = "1"
        self.data = "1"

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:4] == ("shell", "settings", "get", "global"):
            return self.wifi if args[4] == "wifi_on" else self.data
        if args == ("shell", "dumpsys", "battery"):
            return "UPDATES STOPPED" if self.override else "USB powered: true"
        if args == ("shell", "dumpsys", "deviceidle"):
            return "mForceIdle=false"
        if args == ("shell", "cmd", "deviceidle", "get", "deep"):
            return self.idle
        if args == ("shell", "cmd", "deviceidle", "force-idle", "deep"):
            self.idle = "IDLE"
        if args == ("shell", "cmd", "deviceidle", "unforce"):
            if self.fail_unforce:
                raise RuntimeError("TEST unforce failed")
            self.idle = "ACTIVE"
        if args == ("shell", "dumpsys", "battery", "unplug"):
            self.override = True
        if args == ("shell", "dumpsys", "battery", "reset"):
            self.override = False
        if args[:3] == ("shell", "svc", "wifi"):
            self.wifi = "1" if args[3] == "enable" else "0"
        if args[:3] == ("shell", "svc", "data"):
            self.data = "1" if args[3] == "enable" else "0"
        return ""


def test_preexisting_battery_override_is_not_owned_or_reset() -> None:
    adb = Adb()
    adb.override = True
    with pytest.raises(RuntimeError, match="existing_battery_or_idle_override"):
        device.DeviceScenarios(adb)
    assert ("shell", "dumpsys", "battery", "reset") not in adb.calls


def test_forced_idle_observation_failure_still_restores_battery_and_display() -> None:
    adb = Adb()
    scenarios = device.DeviceScenarios(adb, pause=lambda _: None)

    def broken_observation() -> dict[str, Any]:
        raise RuntimeError("TEST observation unavailable")

    with pytest.raises(RuntimeError, match="TEST observation unavailable"):
        scenarios.suspend("forced_doze", broken_observation)
    assert not adb.override and adb.idle == "ACTIVE"
    assert ("shell", "input", "keyevent", "224") in adb.calls
    assert scenarios.last_observation["deep_idle_observed"] is True
    assert all(scenarios.last_observation["restoration"].values())


def test_one_restore_failure_does_not_skip_other_restoration() -> None:
    adb = Adb()
    scenarios = device.DeviceScenarios(adb, pause=lambda _: None)
    scenarios.idle_owned = True
    scenarios.battery_owned = True
    scenarios.display_owned = True
    adb.fail_unforce = True
    result = scenarios.restore()
    assert not result["idle_unforced"]
    assert result["battery_reset"] and result["display_woken"] and result["activity_resumed"]


def test_network_loss_and_restore_preserve_original_switches() -> None:
    adb = Adb()
    waits: list[float] = []
    scenarios = device.DeviceScenarios(adb, pause=waits.append)
    scenarios.network("loss")
    assert adb.wifi == adb.data == "0" and waits == [15]
    assert all(scenarios.restore().values())
    assert adb.wifi == adb.data == "1"


def test_missing_mobile_baseline_refuses_network_mutation() -> None:
    adb = Adb()
    adb.data = "0"
    scenarios = device.DeviceScenarios(adb, pause=lambda _: None)
    with pytest.raises(RuntimeError, match="positive_wifi_and_mobile_baselines_required"):
        scenarios.network("mobile")
    assert not any(args[:2] == ("shell", "svc") for args in adb.calls)


@pytest.mark.parametrize(
    ("wifi", "telephony", "confirmed"),
    [
        ("Wifi is disabled", "mDataConnectionState=0", True),
        ("Wi-Fi is disabled", "mDataConnectionState=0\nmDataConnectionState=0", True),
        ("Wifi is enabled", "mDataConnectionState=0", False),
        ("Wifi is disabled", "mDataConnectionState=2", False),
        ("Wifi is disabled", "", False),
    ],
)
def test_radio_loss_requires_both_actual_states(wifi: str, telephony: str, confirmed: bool) -> None:
    assert device.radio_loss_confirmed(wifi, telephony) is confirmed


def test_natural_idle_requires_new_unforced_event() -> None:
    base = (
        "Idling history:\n      deep-idle: -7m44s993ms\n"
        "  Whitelist\nmDeepEnabled=true\nmForceIdle=false"
    )
    old = (
        "Idling history:\n      deep-idle: -15m44s993ms\n"
        "  Whitelist\nmDeepEnabled=true\nmForceIdle=false"
    )
    new = (
        "Idling history:\n      deep-idle: -15m44s993ms\n"
        "      deep-idle: -2m10s2ms\n  Whitelist\nmDeepEnabled=true\nmForceIdle=false"
    )
    assert device.last_deep_idle_age_seconds(base) == pytest.approx(464.993)
    assert not device.natural_deep_idle_observed(base, old, 480)
    assert device.natural_deep_idle_observed(base, new, 480)
    assert not device.natural_deep_idle_observed(
        base, new.replace("mForceIdle=false", "mForceIdle=true"), 480
    )


def test_phone_diagnostics_distinguish_sleep_resume_and_native_state() -> None:
    content = "\n".join(
        json.dumps(item)
        for item in (
            {
                "event": "stage",
                "stage": "natural_sleep_wait",
                "elapsed_realtime_ms": 1000,
                "uptime_ms": 900,
                "pid": 42,
            },
            {
                "event": "activity_resumed",
                "stage": "natural_sleep_wait",
                "elapsed_realtime_ms": 490000,
                "uptime_ms": 1200,
                "pid": 42,
            },
            {
                "event": "native_snapshot",
                "stage": "natural_sleep_wake",
                "elapsed_realtime_ms": 490100,
                "uptime_ms": 1300,
                "pid": 42,
                "native": {
                    "up": False,
                    "network": False,
                    "handshake_age_seconds": -1,
                    "remaining_ms": 0,
                },
            },
        )
    )

    parsed = device.parse_phone_diagnostics(content)

    assert parsed["valid"]
    assert parsed["last_stage"] == "natural_sleep_wait"
    assert parsed["last_observed_stage"] == "natural_sleep_wake"
    assert parsed["activity_resumed_after_sleep_wait"]
    assert parsed["max_approx_suspended_interval_ms"] == 488700
    assert parsed["distinct_process_ids"] == [42]
    assert parsed["events"][-1]["native"]["up"] is False


def test_phone_diagnostics_reject_bad_records_without_echoing_them() -> None:
    parsed = device.parse_phone_diagnostics(
        '{"event":"activity_resumed","stage":"natural_sleep_wait",'
        '"elapsed_realtime_ms":1,"uptime_ms":1,"pid":1,"secret":"do-not-copy"}\n'
        "{not-json"
    )

    assert not parsed["valid"]
    assert parsed["invalid_line_count"] == 2
    assert parsed["events"] == []
    assert "secret" not in json.dumps(parsed)


def test_phone_diagnostics_enforce_bounded_file_size() -> None:
    parsed = device.parse_phone_diagnostics("x" * 32769)
    assert parsed == {"valid": False, "reason": "size_limit", "events": []}

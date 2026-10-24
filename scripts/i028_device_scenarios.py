"""TEST ONLY physical Android actions with bounded waits and owned-state restoration.

No credentials, raw dumps, subscriber IDs or user network names are retained.
Caller owns staging/settlement and must inspect actual tunnel/gate/probe evidence.
"""

import re
import time
from collections.abc import Callable
from typing import Any


def radio_loss_confirmed(wifi_status: str, telephony_status: str) -> bool:
    """Require two actual radio states; never retain raw dumps."""
    states = re.findall(r"\bmDataConnectionState\s*=\s*(\d+)\b", telephony_status)
    return bool(
        re.search(r"\bWi-?Fi is disabled\b", wifi_status, flags=re.IGNORECASE)
        and states
        and all(state == "0" for state in states)
    )


def last_deep_idle_age_seconds(dump: str) -> float | None:
    """Read only the age of the newest deep-idle history event."""
    history = dump.partition("Idling history:")[2].partition("Whitelist")[0]
    ages: list[float] = []
    for match in re.finditer(
        r"deep-idle:\s*-(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+)ms)?",
        history,
    ):
        days, hours, minutes, seconds, millis = (int(value or 0) for value in match.groups())
        ages.append(days * 86400 + hours * 3600 + minutes * 60 + seconds + millis / 1000)
    return ages[-1] if ages else None


def natural_deep_idle_observed(before: str, after: str, elapsed_seconds: float) -> bool:
    """Reject an old history entry or a force-idle/battery simulation."""
    previous = last_deep_idle_age_seconds(before)
    latest = last_deep_idle_age_seconds(after)
    return bool(
        elapsed_seconds >= 60
        and "mForceIdle=false" in before
        and "mForceIdle=false" in after
        and "mDeepEnabled=true" in before
        and "mDeepEnabled=true" in after
        and latest is not None
        and latest <= elapsed_seconds
        and (previous is None or latest + 20 < previous + elapsed_seconds)
    )


class DeviceScenarios:
    def __init__(self, adb: Callable[..., str], pause: Callable[[float], None] = time.sleep):
        self.adb = adb
        self.pause = pause
        self.wifi = self._switch("wifi_on")
        self.data = self._switch("mobile_data")
        battery = adb("shell", "dumpsys", "battery")
        if (
            "UPDATES STOPPED" in battery
            or adb("shell", "cmd", "deviceidle", "get", "deep").strip() != "ACTIVE"
        ):
            raise RuntimeError("i028_existing_battery_or_idle_override")
        self.battery_owned = False
        self.idle_owned = False
        self.display_owned = False
        self.network_owned = False
        self.last_observation: dict[str, Any] | None = None

    def _switch(self, name: str) -> bool:
        value = self.adb("shell", "settings", "get", "global", name).strip()
        if value not in {"0", "1"}:
            raise RuntimeError("i028_unknown_original_network_switch")
        return value == "1"

    def restore(self) -> dict[str, bool]:
        results = {}
        operations: list[tuple[str, tuple[str, ...]]] = []
        if self.idle_owned:
            operations.append(("idle_unforced", ("shell", "cmd", "deviceidle", "unforce")))
        if self.battery_owned:
            operations.append(("battery_reset", ("shell", "dumpsys", "battery", "reset")))
        if self.network_owned:
            operations.extend(
                [
                    (
                        "data_restored",
                        ("shell", "svc", "data", "enable" if self.data else "disable"),
                    ),
                    (
                        "wifi_restored",
                        ("shell", "svc", "wifi", "enable" if self.wifi else "disable"),
                    ),
                ]
            )
        if self.display_owned:
            operations.extend(
                [
                    ("display_woken", ("shell", "input", "keyevent", "224")),
                    (
                        "activity_resumed",
                        (
                            "shell",
                            "am",
                            "start",
                            "-n",
                            "org.example.vpnclient/org.example.vpnpoc.ClientActivity",
                        ),
                    ),
                ]
            )
        for name, args in operations:
            try:
                self.adb(*args)
                results[name] = True
            except Exception:
                results[name] = False
        try:
            results["network_switches_match_baseline"] = (
                self._switch("wifi_on") == self.wifi and self._switch("mobile_data") == self.data
            )
            if self.idle_owned:
                results["idle_force_released"] = "mForceIdle=false" in self.adb(
                    "shell", "dumpsys", "deviceidle"
                )
            if self.battery_owned:
                results["battery_override_absent"] = "UPDATES STOPPED" not in self.adb(
                    "shell", "dumpsys", "battery"
                )
        except Exception:
            results["restoration_observation_complete"] = False
        return results

    def suspend(self, mode: str, observe: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if mode not in {"background", "screen_off", "forced_doze"}:
            raise RuntimeError("i028_unknown_suspend_scenario")
        result: dict[str, Any] = {"mode": mode, "natural_sleep_acceptance": False}
        self.last_observation = result
        try:
            self.display_owned = True
            self.adb("shell", "input", "keyevent", "3")
            if mode != "background":
                self.adb("shell", "input", "keyevent", "223")
            if mode == "forced_doze":
                self.battery_owned = True
                self.adb("shell", "dumpsys", "battery", "unplug")
                self.idle_owned = True
                self.adb("shell", "cmd", "deviceidle", "force-idle", "deep")
                result["deep_idle_observed"] = (
                    self.adb("shell", "cmd", "deviceidle", "get", "deep").strip() == "IDLE"
                )
                if not result["deep_idle_observed"]:
                    raise RuntimeError("i028_forced_doze_not_entered")
            started = time.monotonic()
            self.pause(100)
            result["elapsed_seconds"] = int(time.monotonic() - started)
            result["server_observation_before_wake"] = observe()
        finally:
            result["restoration"] = self.restore()
        return result

    def network(self, mode: str) -> None:
        if mode not in {"mobile", "wifi", "loss"}:
            raise RuntimeError("i028_unknown_network_scenario")
        if not self.wifi or not self.data:
            raise RuntimeError("i028_positive_wifi_and_mobile_baselines_required")
        self.network_owned = True
        if mode == "loss":
            self.adb("shell", "svc", "wifi", "disable")
            self.adb("shell", "svc", "data", "disable")
            self.pause(15)
        else:
            self.adb("shell", "svc", "data", "enable")
            self.adb("shell", "svc", "wifi", "disable" if mode == "mobile" else "enable")
            self.pause(8)

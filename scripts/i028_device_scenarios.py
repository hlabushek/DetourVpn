"""TEST ONLY physical Android actions with bounded waits and owned-state restoration.

No credentials, raw dumps, subscriber IDs or user network names are retained.
Caller owns staging/settlement and must inspect actual tunnel/gate/probe evidence.
"""

import json
import re
import time
from collections.abc import Callable
from typing import Any

PHONE_DIAGNOSTIC_EVENTS = {
    "stage",
    "activity_created",
    "activity_started",
    "activity_resumed",
    "activity_paused",
    "activity_stopped",
    "activity_destroyed",
    "window_focused",
    "window_unfocused",
    "native_snapshot",
}


def parse_phone_diagnostics(content: str) -> dict[str, Any]:
    """Validate and summarize the bounded, app-private NaturalSleep JSONL log."""
    if len(content.encode("utf-8")) > 32768:
        return {"valid": False, "reason": "size_limit", "events": []}
    events: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in content.splitlines():
        if not line:
            continue
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            invalid_lines += 1
            continue
        if not isinstance(item, dict):
            invalid_lines += 1
            continue
        event = item.get("event")
        stage = item.get("stage")
        elapsed = item.get("elapsed_realtime_ms")
        uptime = item.get("uptime_ms")
        pid = item.get("pid")
        if (
            not isinstance(event, str)
            or event not in PHONE_DIAGNOSTIC_EVENTS
            or not isinstance(stage, str)
            or not re.fullmatch(r"[a-z_]{1,64}", stage)
            or not all(type(value) is int and value >= 0 for value in (elapsed, uptime, pid))
            or pid == 0
        ):
            invalid_lines += 1
            continue
        expected_keys = {"event", "stage", "elapsed_realtime_ms", "uptime_ms", "pid"}
        if event == "native_snapshot":
            expected_keys.add("native")
        if set(item) != expected_keys:
            invalid_lines += 1
            continue
        result: dict[str, Any] = {
            "event": event,
            "stage": stage,
            "elapsed_realtime_ms": elapsed,
            "uptime_ms": uptime,
            "pid": pid,
        }
        if event == "native_snapshot":
            native = item.get("native")
            if (
                not isinstance(native, dict)
                or set(native) != {"up", "network", "handshake_age_seconds", "remaining_ms"}
                or type(native.get("up")) is not bool
                or type(native.get("network")) is not bool
                or type(native.get("handshake_age_seconds")) is not int
                or type(native.get("remaining_ms")) is not int
                or not -1 <= native["handshake_age_seconds"] <= 86400
                or not 0 <= native["remaining_ms"] <= 3600000
            ):
                invalid_lines += 1
                continue
            result["native"] = {
                "up": native["up"],
                "network": native["network"],
                "handshake_age_seconds": native["handshake_age_seconds"],
                "remaining_ms": native["remaining_ms"],
            }
        if events and events[-1]["pid"] == pid:
            previous = events[-1]
            elapsed_delta = elapsed - previous["elapsed_realtime_ms"]
            uptime_delta = uptime - previous["uptime_ms"]
            result["approx_suspended_interval_ms"] = (
                max(0, elapsed_delta - uptime_delta)
                if elapsed_delta >= 0 and uptime_delta >= 0
                else None
            )
        else:
            result["approx_suspended_interval_ms"] = None
        events.append(result)
    last_stage_marker = next(
        (event["stage"] for event in reversed(events) if event["event"] == "stage"), None
    )
    last_observed_stage = events[-1]["stage"] if events else None
    resumed_after_wait = any(
        event["event"] == "activity_resumed"
        and event["stage"] in {"natural_sleep_wait", "natural_sleep_wake", "natural_sleep_recovery"}
        for event in events
    )
    return {
        "valid": bool(events) and invalid_lines == 0,
        "event_count": len(events),
        "invalid_line_count": invalid_lines,
        "last_stage": last_stage_marker,
        "last_observed_stage": last_observed_stage,
        "activity_resumed_after_sleep_wait": resumed_after_wait,
        "max_approx_suspended_interval_ms": max(
            (event["approx_suspended_interval_ms"] or 0 for event in events), default=0
        ),
        "distinct_process_ids": sorted({event["pid"] for event in events}),
        "events": events,
    }


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

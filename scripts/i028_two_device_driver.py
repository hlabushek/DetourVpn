"""Fail-closed primitives used by the bounded I-028 two-device driver."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal

Role = Literal["a", "b"]
ROLES: tuple[Role, Role] = ("a", "b")
TwoDevicePhase = Literal["seed", "seed_run", "run", "cleanup"]
FlutterRole = Literal["a", "b", "auto"]


def safe_cleanup_failure_code(error: BaseException) -> str:
    """Return an allowlisted cleanup error category without exposing command output."""
    if isinstance(error, subprocess.TimeoutExpired):
        return "command_timeout"
    if isinstance(error, OSError):
        return "local_transport_error"
    if isinstance(error, RuntimeError):
        known_codes = {
            "i028_bounded_cleanup_retries_exhausted": "retry_budget_exhausted",
            "i028_staging_stop_contract_failed": "stop_response_incomplete",
            "i028_independent_postcheck_failed": "postcheck_incomplete",
        }
        return known_codes.get(str(error), "cleanup_operation_failed")
    return "unexpected_cleanup_error"


def safe_probe_failure_code(error: BaseException) -> str:
    """Return an allowlisted read-only probe failure category without raw details."""
    if isinstance(error, subprocess.TimeoutExpired):
        return "command_timeout"
    if isinstance(error, OSError):
        return "adb_transport_error"
    if isinstance(error, ValueError):
        return "instrumentation_output_invalid"
    if isinstance(error, RuntimeError):
        if str(error) == "i028_bounded_readonly_retries_exhausted":
            return "retry_budget_exhausted"
        if str(error) == "i028_two_device_probe_instrumentation_failed":
            return "instrumentation_command_failed"
        return "probe_command_failed"
    return "unexpected_probe_error"


def parse_i028_postcheck(output: bytes) -> tuple[str, tuple[str, ...]]:
    """Keep only the timer state and fixed I-028 resource marker names."""
    lines = output.splitlines()
    timer_state = lines[0].decode("ascii", "ignore").strip() if lines else "missing"
    if timer_state not in {"inactive", "active", "failed", "missing"}:
        timer_state = "unrecognized"
    markers = {
        "slot_link": b"dv3slot",
        "slot_one_host_link": b"dv3h1",
        "slot_two_host_link": b"dv3h2",
        "slot_one_port": b":51901 ",
        "slot_two_port": b":51902 ",
        "relay_port": b":18443 ",
    }
    present = tuple(name for name, marker in markers.items() if marker in output)
    return timer_state, present


def i028_cleanup_steps_passed(steps: Mapping[str, Any]) -> bool:
    """Validate cleanup booleans and the expected empty remote resource snapshot."""
    required_true = (
        "staging_a_closed",
        "independent_remote_postcheck",
        "local_reverse_tunnel_closed",
        "local_api_stopped",
        "device_0_restored",
        "device_1_restored",
    )
    return (
        all(steps.get(name) is True for name in required_true)
        and steps.get("postcheck_timer_state") == "inactive"
        and steps.get("postcheck_resource_markers") == []
    )


def retry_bounded_cleanup(
    action: Callable[[], Any],
    *,
    attempts: int = 3,
    delay_seconds: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Retry only an idempotent bounded cleanup operation after transport failure."""
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError("cleanup_attempts_out_of_bounds")
    if type(delay_seconds) is not int or not 0 <= delay_seconds <= 10:
        raise ValueError("cleanup_retry_delay_out_of_bounds")
    for attempt in range(attempts):
        try:
            return action()
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            if attempt + 1 < attempts:
                sleep(delay_seconds)
    raise RuntimeError("i028_bounded_cleanup_retries_exhausted") from None


def retry_bounded_readonly(
    action: Callable[[], Any],
    *,
    attempts: int = 2,
    delay_seconds: int = 1,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Retry a transient read-only preflight once; never repeat device mutations."""
    if type(attempts) is not int or not 1 <= attempts <= 2:
        raise ValueError("readonly_attempts_out_of_bounds")
    if type(delay_seconds) is not int or not 0 <= delay_seconds <= 1:
        raise ValueError("readonly_retry_delay_out_of_bounds")
    for attempt in range(attempts):
        try:
            return action()
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            if attempt + 1 < attempts:
                sleep(delay_seconds)
    raise RuntimeError("i028_bounded_readonly_retries_exhausted") from None


def unexpected_flutter_exit_roles(
    return_codes: Sequence[int | None], *, completed_roles: frozenset[Role] = frozenset()
) -> tuple[Role, ...]:
    """Ignore successful exits only for roles whose disconnect was confirmed."""
    if len(return_codes) != len(ROLES):
        raise ValueError("exactly_two_flutter_processes_required")
    return tuple(
        role
        for role, return_code in zip(ROLES, return_codes, strict=True)
        if return_code is not None and (role not in completed_roles or return_code != 0)
    )


def probe_confirms_staging_vpn(result: Mapping[str, object]) -> bool:
    """Require a complete independent-UID probe through the staging VPN."""
    return all(
        result.get(flag) is True
        for flag in (
            "probe_complete",
            "application_initialized",
            "process_uid_matches_target",
            "active_network_available",
            "active_network_validated",
            "active_network_is_vpn",
            "ipv4_available",
            "egress_is_staging",
        )
    )


def two_device_start_barrier_ready(
    roles: Mapping[str, object], appop_ready_roles: Sequence[str]
) -> bool:
    """Require both isolated identities and both VPN app-ops before connecting."""
    return all(role in roles for role in ROLES) and all(
        role in appop_ready_roles for role in ROLES
    )


def android_install_hash_matches(installed_sha256: str | None, expected_sha256: str) -> bool:
    """Recognize only the exact source-bound APK after a delayed ADB install."""
    return (
        installed_sha256 is not None
        and re.fullmatch(r"[0-9a-f]{64}", installed_sha256) is not None
        and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None
        and installed_sha256 == expected_sha256
    )


def android_install_user_restriction_retryable(
    categories: Sequence[str], *, package_absent: bool
) -> bool:
    """Retry one user-restricted APK install only when Android confirms absence."""
    return package_absent and "user_restricted" in categories


def two_device_flutter_test_args(
    flutter: str,
    serial: str,
    *,
    phase: TwoDevicePhase,
    role: FlutterRole,
    package: str,
    verbose: bool = False,
) -> list[str]:
    """Build only `flutter test` options; Gradle project properties come from env."""
    if not serial or serial.startswith("emulator-"):
        raise ValueError("physical_device_serial_required")
    if package != "com.detourvpn.client.i028":
        raise ValueError("isolated_i028_package_required")
    if phase not in {"seed", "seed_run", "run", "cleanup"} or role not in {
        "a",
        "b",
        "auto",
    }:
        raise ValueError("invalid_two_device_flutter_phase")
    args = [
        flutter,
        "test",
        "integration_test/i028_two_device_test.dart",
        "-d",
        serial,
        "--no-pub",
        "--no-uninstall",
        "--dart-define=VPN_WINDOW_TUNNELS=true",
        "--dart-define=VPN_I028_TWO_PHASE=" + phase,
        "--dart-define=VPN_I028_ROLE=" + role,
        "--dart-define=VPN_I028_APP_PACKAGE=" + package,
    ]
    if verbose:
        args.append("--verbose")
    return args


def safe_i028_flutter_markers(*outputs: bytes) -> tuple[str, ...]:
    """Return only fixed-format I-028 error markers from untrusted Flutter logs."""
    allowed = {
        "configuration",
        "package",
        "existing_session",
        "session_not_saved",
        "session_missing",
        "seed_barrier_timeout",
        "seed_run_timeout",
        "role_unavailable",
        "node_unavailable",
        "tunnel_not_confirmed",
        "native_down",
        "lease_missing",
        "network_unvalidated",
        "handshake_missing",
        "handshake_stale",
        "lease_expired",
        "connect_unconfirmed",
        "connection_list",
        "window_missing",
        "command_invalid",
        "unexpected_native_down",
        "command_timeout",
        "native_disconnect_unconfirmed",
        "remote_revoke_unconfirmed",
        "phase_failed",
        "local_cleanup_failed",
    }
    markers = {
        match.decode("ascii")
        for output in outputs
        for match in re.findall(rb"\bi028_two_device_[a-z0-9_]{1,96}\b", output)
        if match.decode("ascii").removeprefix("i028_two_device_") in allowed
        or re.fullmatch(rb"i028_two_device_client_error_[a-z_]{1,64}", match)
    }
    return tuple(sorted(markers))


def safe_flutter_failure_categories(*outputs: bytes) -> tuple[str, ...]:
    """Classify common Flutter/Android failures without persisting their log text."""
    combined = b"\n".join(outputs).lower()
    patterns = {
        "flutter_usage_error": rb"could not find an option named|usage: flutter",
        "gradle_build_failure": (
            rb"failure: build failed|execution failed for task|gradle task .* failed"
        ),
        "android_install_failure": (
            rb"install_failed|failed to install|package manager returned failure"
        ),
        "device_transport_failure": (
            rb"device offline|no connected devices|device .* not found|unauthorized device"
        ),
        "app_launch_failure": (
            rb"error connecting to the service protocol|waiting for a debug connection|"
            rb"failed to launch"
        ),
        "test_failure": (
            rb"some tests failed|test failure|test(s)? failed|failure details|expected:.*actual:"
        ),
        "flutter_error": rb"\berror:|unhandled exception|fatal exception",
    }
    return tuple(
        sorted(category for category, pattern in patterns.items() if re.search(pattern, combined))
    )


def safe_flutter_failure_details(*outputs: bytes) -> dict[str, tuple[str, ...]]:
    """Keep only bounded Gradle task names and fixed cause categories."""
    combined = b"\n".join(outputs)
    task_patterns = (
        rb"execution failed for task\s+['\"](:[a-z0-9_.:-]{1,120})['\"]",
        rb"gradle task\s+([a-z][a-z0-9_.:-]{0,119})\s+failed\b",
    )
    failed_tasks = {
        match.decode("ascii")
        for pattern in task_patterns
        for match in re.findall(pattern, combined, re.IGNORECASE)
    }
    causes = {
        "android_sdk_configuration": (
            rb"sdk location not found|failed to find target with hash string|"
            rb"android sdk not found"
        ),
        "android_test_runner_class_missing": (
            rb"(?:classnotfoundexception|noclassdeffounderror)[^\r\n]{0,160}"
            rb"androidx\.test\.runner\.androidjunitrunner"
        ),
        "dependency_resolution": (
            rb"could not resolve all files|no matching variant of|"
            rb"could not find .* required by"
        ),
        "java_compile_failure": (
            rb"compilation failed.*java|compile[a-z0-9]*jav[a]?withjavac.*failed"
        ),
        "kotlin_compile_failure": rb"kotlin compilation error|compile[a-z0-9]*kotlin.*failed",
        "manifest_merge_failure": rb"manifest merger failed|uses-sdk:minSdkVersion",
        "resource_link_failure": rb"failed linking references|aapt2.*error",
    }
    known_causes = tuple(
        sorted(
            name for name, pattern in causes.items() if re.search(pattern, combined, re.IGNORECASE)
        )
    )
    return {
        "failed_gradle_tasks": tuple(sorted(failed_tasks)[:8]),
        "known_causes": known_causes,
    }


def safe_android_install_failure_categories(*outputs: bytes) -> tuple[str, ...]:
    """Classify only fixed Android package-installer failure codes."""
    combined = b"\n".join(outputs).lower()
    patterns = {
        "user_restricted": rb"install_failed_user_restricted|user_restricted",
        "install_aborted": rb"install_failed_aborted|install_failed_internal_error",
        "insufficient_storage": rb"install_failed_insufficient_storage|not enough storage",
        "invalid_apk": rb"install_failed_invalid_apk|install_parse_failed",
        "update_incompatible": rb"install_failed_update_incompatible",
        "version_downgrade": rb"install_failed_version_downgrade",
        "no_matching_abis": rb"install_failed_no_matching_abis",
        "permission_model_downgrade": rb"install_failed_permission_model_downgrade",
    }
    categories = tuple(
        sorted(name for name, pattern in patterns.items() if re.search(pattern, combined))
    )
    return categories or ("other_install_failure",)


def exactly_two_physical_serials(lines: list[str]) -> tuple[str, str]:
    """Parse `adb devices -l`; reject emulators and every ambiguous inventory."""
    serials: list[str] = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device" and not fields[0].startswith("emulator-"):
            serials.append(fields[0])
    if len(serials) != 2 or len(set(serials)) != 2:
        raise ValueError("exactly_two_physical_android_devices_required")
    return serials[0], serials[1]


@dataclass
class DeviceRecord:
    """Sanitized per-role facts; bearer tokens and keys are intentionally excluded."""

    user_id: str | None = None
    device_id: str | None = None
    family_id: str | None = None
    connection_id: str | None = None
    window_id: str | None = None
    slot_id: int | None = None
    connected: bool = False
    disconnected: bool = False
    measured_bytes: int = 0


@dataclass
class TwoDeviceCoordinator:
    """Thread-safe barriers and ownership checks for the physical driver."""

    records: dict[Role, DeviceRecord] = field(
        default_factory=lambda: {role: DeviceRecord() for role in ROLES}
    )
    _lock: Lock = field(default_factory=Lock, repr=False)

    def bind(self, role: Role, *, user_id: str, device_id: str, family_id: str) -> None:
        with self._lock:
            row = self.records[role]
            if row.user_id is not None:
                raise ValueError("role_identity_already_bound")
            row.user_id, row.device_id, row.family_id = user_id, device_id, family_id
            other = self.records["b" if role == "a" else "a"]
            if any(
                left is not None and left == right
                for left, right in (
                    (row.user_id, other.user_id),
                    (row.device_id, other.device_id),
                    (row.family_id, other.family_id),
                )
            ):
                row.user_id = row.device_id = row.family_id = None
                raise ValueError("two_device_identity_collision")

    def connected(
        self,
        role: Role,
        *,
        connection_id: str,
        window_id: str,
        slot_id: int,
    ) -> bool:
        """Record a role's live lease; return True only when both roles reached barrier."""
        with self._lock:
            row = self.records[role]
            if row.user_id is None or row.connected or row.disconnected or slot_id < 1:
                raise ValueError("invalid_two_device_connection_transition")
            row.connection_id, row.window_id, row.slot_id = connection_id, window_id, slot_id
            other = self.records["b" if role == "a" else "a"]
            row.connected = True
            if other.connected and (
                row.connection_id == other.connection_id
                or row.window_id == other.window_id
                or row.slot_id == other.slot_id
            ):
                row.connected = False
                row.connection_id = row.window_id = None
                row.slot_id = None
                raise ValueError("two_device_connection_collision")
            return all(self.records[item].connected for item in ROLES)

    def record_transfer(self, role: Role, byte_count: int) -> None:
        if type(byte_count) is not int or byte_count <= 0:
            raise ValueError("positive_measured_bytes_required")
        with self._lock:
            row = self.records[role]
            if not row.connected or row.disconnected:
                raise ValueError("transfer_outside_live_connection")
            row.measured_bytes += byte_count

    def disconnected(self, role: Role) -> None:
        with self._lock:
            row = self.records[role]
            if not row.connected or row.disconnected:
                raise ValueError("invalid_two_device_disconnect_transition")
            row.disconnected = True

    def snapshot(self) -> dict[str, dict[str, str | int | bool | None]]:
        with self._lock:
            return {
                role: {
                    "user_id": row.user_id,
                    "device_id": row.device_id,
                    "family_id": row.family_id,
                    "connection_id": row.connection_id,
                    "window_id": row.window_id,
                    "slot_id": row.slot_id,
                    "connected": row.connected,
                    "disconnected": row.disconnected,
                    "measured_bytes": row.measured_bytes,
                }
                for role, row in self.records.items()
            }


def cleanup_owned_packages(
    *, initial_packages: set[str], current_packages: set[str], owned_names: set[str]
) -> tuple[str, ...]:
    """Return only packages absent at baseline and still present now; never erase user state."""
    if not owned_names <= current_packages:
        raise ValueError("owned_package_state_changed_or_unavailable")
    if owned_names & initial_packages:
        raise ValueError("preexisting_test_package_preserved")
    return tuple(sorted(owned_names))

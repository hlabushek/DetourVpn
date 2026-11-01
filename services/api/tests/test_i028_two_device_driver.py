"""Deterministic tests for I-028's two-device coordinator and fail-closed cleanup."""

import importlib.util
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from scripts.i028_two_device_driver import (
    TwoDeviceCoordinator,
    android_install_hash_matches,
    android_install_user_restriction_retryable,
    cleanup_owned_packages,
    exactly_two_physical_serials,
    i028_cleanup_steps_passed,
    parse_i028_postcheck,
    probe_confirms_staging_vpn,
    retry_bounded_cleanup,
    retry_bounded_readonly,
    safe_android_install_failure_categories,
    safe_cleanup_failure_code,
    safe_flutter_failure_categories,
    safe_flutter_failure_details,
    safe_i028_flutter_markers,
    safe_probe_failure_code,
    two_device_flutter_test_args,
    two_device_start_barrier_ready,
    unexpected_flutter_exit_roles,
)


def test_flutter_diagnostics_keep_only_safe_i028_error_markers() -> None:
    stdout = b"failure i028_two_device_client_error_network_unavailable token=secret"
    stderr = (
        b"test/integration_test/i028_two_device_test.dart token=secret "
        b"i028_two_device_phase_failed i028_two_device_native_disconnect_unconfirmed "
        b"i028_two_device_seed_barrier_timeout "
        b"i028_two_device_remote_revoke_unconfirmed i028_two_device_native_down "
        b"i028_two_device_handshake_missing i028_two_device_lease_expired"
    )
    assert safe_i028_flutter_markers(stdout, stderr) == (
        "i028_two_device_client_error_network_unavailable",
        "i028_two_device_handshake_missing",
        "i028_two_device_lease_expired",
        "i028_two_device_native_disconnect_unconfirmed",
        "i028_two_device_native_down",
        "i028_two_device_phase_failed",
        "i028_two_device_remote_revoke_unconfirmed",
        "i028_two_device_seed_barrier_timeout",
    )


def test_flutter_diagnostics_classify_output_without_retaining_it() -> None:
    stdout = b"FAILURE: Build failed with an exception. private-path secret-token"
    stderr = b"Some tests failed. private-path secret-token"
    assert safe_flutter_failure_categories(stdout, stderr) == (
        "gradle_build_failure",
        "test_failure",
    )


def test_cleanup_receipt_accepts_expected_empty_resource_snapshot() -> None:
    steps = {
        "staging_a_closed": True,
        "independent_remote_postcheck": True,
        "local_reverse_tunnel_closed": True,
        "local_api_stopped": True,
        "device_0_restored": True,
        "device_1_restored": True,
        "postcheck_timer_state": "inactive",
        "postcheck_resource_markers": [],
    }
    assert i028_cleanup_steps_passed(steps)
    assert not i028_cleanup_steps_passed({**steps, "postcheck_resource_markers": ["relay_port"]})
    assert not i028_cleanup_steps_passed({**steps, "device_1_restored": False})


def test_android_install_failure_diagnostics_keep_only_fixed_categories() -> None:
    output = (
        b"Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user] "
        b"private-serial=secret local-path=C:\\private\\app.apk"
    )
    details = safe_android_install_failure_categories(output)
    assert details == ("user_restricted",)
    assert "secret" not in repr(details)
    assert "C:\\private" not in repr(details)
    assert safe_android_install_failure_categories(b"unknown package installer error") == (
        "other_install_failure",
    )


def test_flutter_test_arguments_avoid_build_only_gradle_flags() -> None:
    for phase, role in (
        ("seed", "a"),
        ("seed_run", "a"),
        ("run", "auto"),
        ("cleanup", "b"),
    ):
        args = two_device_flutter_test_args(
            "flutter",
            "physical-device",
            phase=phase,
            role=role,
            package="org.example.vpnclient.i028",
        )
        assert args[:3] == ["flutter", "test", "integration_test/i028_two_device_test.dart"]
        assert f"--dart-define=VPN_I028_TWO_PHASE={phase}" in args
        assert f"--dart-define=VPN_I028_ROLE={role}" in args
        assert not any(arg.startswith("--android-project-arg") for arg in args)
        assert "--verbose" not in args
    with pytest.raises(ValueError, match="physical_device_serial"):
        two_device_flutter_test_args(
            "flutter",
            "emulator-5554",
            phase="seed",
            role="a",
            package="org.example.vpnclient.i028",
        )


def test_start_barrier_waits_for_both_identities_and_vpn_appops() -> None:
    roles = {"a": object(), "b": object()}
    assert not two_device_start_barrier_ready({}, [])
    assert not two_device_start_barrier_ready({"a": object()}, ["a"])
    assert not two_device_start_barrier_ready(roles, ["a"])
    assert two_device_start_barrier_ready(roles, ["a", "b"])


def test_flutter_seed_can_collect_verbose_output_without_persisting_it() -> None:
    args = two_device_flutter_test_args(
        "flutter",
        "physical-device",
        phase="seed",
        role="a",
        package="org.example.vpnclient.i028",
        verbose=True,
    )
    assert args[-1] == "--verbose"


def test_successful_a_exit_is_expected_only_after_a_disconnect_is_confirmed() -> None:
    assert unexpected_flutter_exit_roles((None, None)) == ()
    assert unexpected_flutter_exit_roles((0, None), completed_roles=frozenset({"a"})) == ()
    assert unexpected_flutter_exit_roles((0, None)) == ("a",)
    assert unexpected_flutter_exit_roles((0, 0), completed_roles=frozenset({"a"})) == ("b",)
    assert unexpected_flutter_exit_roles((1, None), completed_roles=frozenset({"a"})) == ("a",)
    assert unexpected_flutter_exit_roles((0, 1), completed_roles=frozenset({"a"})) == ("b",)


def test_flutter_exit_classification_requires_both_device_processes() -> None:
    with pytest.raises(ValueError, match="exactly_two_flutter_processes"):
        unexpected_flutter_exit_roles((None,))


def test_staging_probe_requires_vpn_uid_and_real_staging_egress() -> None:
    result = {
        "probe_complete": True,
        "application_initialized": True,
        "process_uid_matches_target": True,
        "active_network_available": True,
        "active_network_validated": True,
        "active_network_is_vpn": True,
        "ipv4_available": True,
        "egress_is_staging": True,
    }
    assert probe_confirms_staging_vpn(result)
    for flag in result:
        incomplete = dict(result)
        incomplete[flag] = False
        assert not probe_confirms_staging_vpn(incomplete)


def test_late_adb_install_is_accepted_only_for_the_exact_artifact_hash() -> None:
    expected = "a" * 64
    assert android_install_hash_matches(expected, expected)
    assert not android_install_hash_matches(None, expected)
    assert not android_install_hash_matches("b" * 64, expected)
    assert not android_install_hash_matches("private-path", expected)


def test_user_restricted_install_retries_only_when_exact_package_is_absent() -> None:
    assert android_install_user_restriction_retryable(("user_restricted",), package_absent=True)
    assert not android_install_user_restriction_retryable(
        ("user_restricted",), package_absent=False
    )
    assert not android_install_user_restriction_retryable(("invalid_apk",), package_absent=True)


def test_bounded_cleanup_retries_transient_transport_failures() -> None:
    calls = 0
    delays: list[float] = []

    def cleanup() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("private transport detail")
        return "closed"

    assert retry_bounded_cleanup(cleanup, sleep=delays.append) == "closed"
    assert calls == 3
    assert delays == [5, 5]


def test_bounded_cleanup_exhaustion_is_fixed_and_does_not_leak_errors() -> None:
    calls = 0
    delays: list[float] = []

    def cleanup() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private host and token")

    with pytest.raises(RuntimeError, match="i028_bounded_cleanup_retries_exhausted") as error:
        retry_bounded_cleanup(cleanup, sleep=delays.append)
    assert "private" not in str(error.value)
    assert calls == 3
    assert delays == [5, 5]


def test_bounded_cleanup_rejects_unbounded_retry_parameters() -> None:
    with pytest.raises(ValueError, match="cleanup_attempts_out_of_bounds"):
        retry_bounded_cleanup(lambda: None, attempts=6)
    with pytest.raises(ValueError, match="cleanup_retry_delay_out_of_bounds"):
        retry_bounded_cleanup(lambda: None, delay_seconds=11)


def test_cleanup_failure_diagnostics_are_bounded_and_redacted() -> None:
    assert (
        safe_cleanup_failure_code(RuntimeError("i028_bounded_cleanup_retries_exhausted"))
        == "retry_budget_exhausted"
    )
    assert safe_cleanup_failure_code(subprocess.TimeoutExpired("ssh", 30)) == "command_timeout"
    assert safe_cleanup_failure_code(RuntimeError("private host and token")) == (
        "cleanup_operation_failed"
    )


def test_probe_failure_diagnostics_are_bounded_and_redacted() -> None:
    assert (
        safe_probe_failure_code(RuntimeError("i028_bounded_readonly_retries_exhausted"))
        == "retry_budget_exhausted"
    )
    assert safe_probe_failure_code(RuntimeError("private serial and token")) == (
        "probe_command_failed"
    )
    assert safe_probe_failure_code(ValueError("private probe output")) == (
        "instrumentation_output_invalid"
    )


def test_i028_postcheck_keeps_only_timer_state_and_fixed_marker_names() -> None:
    assert parse_i028_postcheck(b"inactive\n") == ("inactive", ())
    assert parse_i028_postcheck(b"active\n0.0.0.0:18443 ") == ("active", ("relay_port",))
    assert parse_i028_postcheck(b"") == ("missing", ())
    assert parse_i028_postcheck(b"private host token") == ("unrecognized", ())


def test_readonly_preflight_retries_once_with_a_fixed_failure() -> None:
    calls = 0
    delays: list[float] = []

    def inventory() -> tuple[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient ADB server output")
        return ("pixel", "tablet")

    assert retry_bounded_readonly(inventory, sleep=delays.append) == ("pixel", "tablet")
    assert calls == 2
    assert delays == [1]


def test_readonly_preflight_exhaustion_is_bounded_and_sanitized() -> None:
    calls = 0
    delays: list[float] = []

    def inventory() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private adb output")

    with pytest.raises(RuntimeError, match="i028_bounded_readonly_retries_exhausted") as error:
        retry_bounded_readonly(inventory, sleep=delays.append)
    assert "private" not in str(error.value)
    assert calls == 2
    assert delays == [1]


def test_readonly_preflight_rejects_unbounded_retry_parameters() -> None:
    with pytest.raises(ValueError, match="readonly_attempts_out_of_bounds"):
        retry_bounded_readonly(lambda: None, attempts=3)
    with pytest.raises(ValueError, match="readonly_retry_delay_out_of_bounds"):
        retry_bounded_readonly(lambda: None, delay_seconds=2)


def test_idempotent_runtime_cleanup_returns_the_recorded_cleanup_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_path = Path(__file__).resolve().parents[3] / "infra/staging-v3/runtime.py"
    spec = importlib.util.spec_from_file_location("i028_runtime_cleanup_test", runtime_path)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "STATE_DIR", tmp_path)
    monkeypatch.setattr(runtime, "disarm_timeout", lambda: True)
    (tmp_path / "i028-cleanup.json").write_text(
        json.dumps({"cleanup_complete": True, "gate_close_errors_before_core_stop": 0}),
        encoding="utf-8",
    )

    result = runtime.cleanup()

    assert result == {
        "cleanup_complete": True,
        "timeout_timer_disarmed": True,
        "journal_preserved": True,
        "temporary_grant_removed": True,
        "gate_close_errors_before_core_stop": 0,
    }


def test_idempotent_runtime_cleanup_rejects_incomplete_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_path = Path(__file__).resolve().parents[3] / "infra/staging-v3/runtime.py"
    spec = importlib.util.spec_from_file_location(
        "i028_runtime_cleanup_test_incomplete", runtime_path
    )
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "STATE_DIR", tmp_path)
    (tmp_path / "i028-cleanup.json").write_text('{"cleanup_complete":true}', encoding="utf-8")

    with pytest.raises(runtime.Failure, match="cleanup_journal_incomplete"):
        runtime.cleanup()


def test_flutter_failure_details_keep_only_safe_gradle_facts() -> None:
    output = (
        b"Execution failed for task ':app:assembleDebugAndroidTest'. "
        b"Caused by: java.lang.NoClassDefFoundError: "
        b"androidx.test.runner.AndroidJUnitRunner token=private-value "
        b"C:\\Users\\private\\project"
    )
    details = safe_flutter_failure_details(output)
    assert details == {
        "failed_gradle_tasks": (":app:assembleDebugAndroidTest",),
        "known_causes": ("android_test_runner_class_missing",),
    }
    assert "private-value" not in repr(details)
    assert "C:\\Users" not in repr(details)


def test_flutter_failure_details_classify_dependency_and_sdk_errors() -> None:
    details = safe_flutter_failure_details(
        b"Could not resolve all files for configuration ':app:debugRuntimeClasspath'. "
        b"SDK location not found. user=private-value"
    )
    assert details == {
        "failed_gradle_tasks": (),
        "known_causes": ("android_sdk_configuration", "dependency_resolution"),
    }


def test_requires_exactly_two_distinct_authorized_physical_devices() -> None:
    inventory = [
        "List of devices attached",
        "phone-a device product:pixel",
        "phone-b device product:tablet",
    ]
    assert exactly_two_physical_serials(inventory) == ("phone-a", "phone-b")
    for bad in (
        inventory[:2],
        [inventory[0], "phone-a device", "phone-a device"],
        [inventory[0], "phone-a unauthorized product:pixel", "phone-b device"],
    ):
        with pytest.raises(ValueError, match="exactly_two"):
            exactly_two_physical_serials(bad)


def test_two_roles_barrier_is_concurrent_and_keeps_identities_and_windows_distinct() -> None:
    coordinator = TwoDeviceCoordinator()
    coordinator.bind("a", user_id="user-a", device_id="device-a", family_id="family-a")
    coordinator.bind("b", user_id="user-b", device_id="device-b", family_id="family-b")
    barrier = Barrier(2)

    def connect(role: str, suffix: str) -> bool:
        barrier.wait(timeout=2)
        return coordinator.connected(
            role,
            connection_id=f"connection-{suffix}",
            window_id=f"window-{suffix}",
            slot_id=int(suffix),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda args: connect(*args), (("a", "1"), ("b", "2"))))

    assert sorted(outcomes) == [False, True]
    coordinator.record_transfer("a", 1234)
    coordinator.record_transfer("b", 5678)
    report = coordinator.snapshot()
    assert report["a"]["measured_bytes"] == 1234
    assert report["b"]["measured_bytes"] == 5678
    assert report["a"]["user_id"] != report["b"]["user_id"]
    assert report["a"]["family_id"] != report["b"]["family_id"]
    assert report["a"]["connection_id"] != report["b"]["connection_id"]
    assert report["a"]["window_id"] != report["b"]["window_id"]
    assert report["a"]["slot_id"] != report["b"]["slot_id"]


def test_rejects_identity_and_window_collisions_and_invalid_transitions() -> None:
    coordinator = TwoDeviceCoordinator()
    coordinator.bind("a", user_id="user-a", device_id="device-a", family_id="family-a")
    with pytest.raises(ValueError, match="identity_collision"):
        coordinator.bind("b", user_id="user-a", device_id="device-b", family_id="family-b")
    coordinator.bind("b", user_id="user-b", device_id="device-b", family_id="family-b")
    assert not coordinator.connected(
        "a", connection_id="connection-a", window_id="window-shared", slot_id=1
    )
    with pytest.raises(ValueError, match="connection_collision"):
        coordinator.connected(
            "b", connection_id="connection-b", window_id="window-shared", slot_id=2
        )
    with pytest.raises(ValueError, match="transfer_outside"):
        coordinator.record_transfer("b", 1)
    with pytest.raises(ValueError, match="positive_measured_bytes"):
        coordinator.record_transfer("a", 0)


def test_cleanup_only_returns_owned_absent_at_baseline_and_fails_closed() -> None:
    assert cleanup_owned_packages(
        initial_packages={"org.example.vpnpoc"},
        current_packages={"org.example.vpnpoc", "org.example.vpnpoc.test"},
        owned_names={"org.example.vpnpoc.test"},
    ) == ("org.example.vpnpoc.test",)
    with pytest.raises(ValueError, match="preexisting"):
        cleanup_owned_packages(
            initial_packages={"org.example.vpnclient.i028"},
            current_packages={"org.example.vpnclient.i028"},
            owned_names={"org.example.vpnclient.i028"},
        )
    with pytest.raises(ValueError, match="state_changed"):
        cleanup_owned_packages(
            initial_packages=set(),
            current_packages=set(),
            owned_names={"org.example.vpnclient.i028"},
        )

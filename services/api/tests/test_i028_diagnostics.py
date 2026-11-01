"""Synthetic diagnostics only: never include credentials or arbitrary native fields."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_i028_live import (
    append_sustained_window_observation,
    bounded_probe_partial_output,
    collect_dns_path_observation,
    i028_suite_config,
    parse_probe,
    record_dns_path_observation,
    recovery_connection_snapshot,
    sanitize_dns_observation,
    sanitize_egress_hold_observations,
    sanitize_egress_renewal_status,
    sanitize_recovery_connection_status,
    sanitize_recovery_node_status,
    summarize_probe_runner,
)

ROOT = Path(__file__).resolve().parents[3]


def test_dns_action_records_unexpected_failure_without_exception_text() -> None:
    report = {"dns_path_action_state": "queued"}

    def fail() -> dict:
        assert report["dns_path_action_state"] == "started"
        raise RuntimeError("synthetic secret must not be retained")

    assert record_dns_path_observation(report, fail) == {"diagnostic_collected": False}
    assert report == {
        "dns_path_action_state": "completed",
        "dns_path_observation": {"complete": False, "stage": "internal", "reason": "unexpected"},
    }
    assert "secret" not in repr(report)


def test_dns_action_retains_incomplete_result_and_rejects_invalid_result() -> None:
    report: dict = {}
    incomplete = {"complete": False, "stage": "ready", "reason": "ready_timeout"}
    assert record_dns_path_observation(report, lambda: incomplete) == {
        "diagnostic_collected": False
    }
    assert report["dns_path_observation"] == incomplete
    assert record_dns_path_observation(report, lambda: {"complete": 1}) == {
        "diagnostic_collected": False
    }
    assert report["dns_path_observation"]["reason"] == "unexpected"


def test_observer_protocol_exchanges_ready_probe_and_bounded_result() -> None:
    child = (
        "import json,sys;print('{\"ready\":true}',flush=True);"
        "assert sys.stdin.buffer.readline()==b'done\\n';"
        "print(json.dumps({'complete':True,'packet_loss':False,"
        "'counts':{'tunnel':{'outbound':1,'inbound':1},"
        "'uplink':{'outbound':1,'inbound':1}}}),flush=True)"
    )
    result = collect_dns_path_observation(
        [sys.executable, "-u", "-c", child], lambda: {"fixed_udp_dns_response": True}
    )
    timing = result.pop("timing_ms")
    assert set(timing) == {"ready_ms", "probe_ms", "finish_ms"}
    assert all(type(value) is int and 0 <= value <= 120000 for value in timing.values())
    assert result == {
        "complete": True,
        "packet_loss": False,
        "probe_response": True,
        "counts": {
            "tunnel": {"outbound": 1, "inbound": 1},
            "uplink": {"outbound": 1, "inbound": 1},
        },
    }


@pytest.mark.parametrize(
    ("child", "expected"),
    [
        (
            'print(\'{"error":"dns_observe_failed","category":"capture_not_ready"}\')',
            "capture_not_ready",
        ),
        ("print('SYNTHETIC PRIVATE MESSAGE')", "invalid_ready"),
        ("import time;time.sleep(2)", "ready_timeout"),
    ],
)
def test_observer_protocol_classifies_ready_failures_without_raw_output(
    child: str, expected: str
) -> None:
    result = collect_dns_path_observation(
        [sys.executable, "-u", "-c", child],
        lambda: {"fixed_udp_dns_response": True},
        ready_timeout=0.5,
    )
    assert 0 <= result.pop("timing_ms")["ready_ms"] <= 120000
    assert result == {"complete": False, "stage": "ready", "reason": expected}
    assert "SYNTHETIC PRIVATE" not in repr(result)


def test_observer_protocol_limits_untrusted_stdout() -> None:
    child = (
        "import sys;print('{\"ready\":true}',flush=True);"
        "sys.stdin.buffer.readline();print('X'*5000,flush=True)"
    )
    result = collect_dns_path_observation(
        [sys.executable, "-u", "-c", child], lambda: {"fixed_udp_dns_response": True}
    )
    assert 0 <= result.pop("timing_ms")["finish_ms"] <= 120000
    assert result == {"complete": False, "stage": "finish", "reason": "output_limit"}


def test_observer_protocol_never_converts_probe_failure_to_success() -> None:
    child = "import sys;print('{\"ready\":true}',flush=True);sys.stdin.buffer.readline()"

    def failed_probe() -> dict:
        raise RuntimeError("SYNTHETIC PRIVATE ERROR")

    result = collect_dns_path_observation([sys.executable, "-u", "-c", child], failed_probe)
    assert 0 <= result.pop("timing_ms")["probe_ms"] <= 120000
    assert result == {"complete": False, "stage": "probe", "reason": "probe_failed"}
    assert "SYNTHETIC PRIVATE" not in repr(result)


def test_observer_protocol_keeps_remote_fixed_failure_after_ready() -> None:
    child = (
        "import sys;print('{\"ready\":true}',flush=True);"
        "sys.stdin.buffer.readline();"
        'print(\'{"error":"dns_observe_failed",'
        '"category":"capture_sync_timeout"}\',flush=True);sys.exit(1)'
    )
    result = collect_dns_path_observation(
        [sys.executable, "-u", "-c", child], lambda: {"fixed_udp_dns_response": False}
    )
    assert 0 <= result.pop("timing_ms")["finish_ms"] <= 120000
    assert result == {"complete": False, "stage": "finish", "reason": "capture_sync_timeout"}


def test_observer_protocol_timeout_is_bounded_and_does_not_leak_output() -> None:
    child = (
        "import sys,time;print('{\"ready\":true}',flush=True);"
        "sys.stdin.buffer.readline();time.sleep(3)"
    )
    result = collect_dns_path_observation(
        [sys.executable, "-u", "-c", child],
        lambda: {"fixed_udp_dns_response": False},
        finish_timeout=0.5,
    )
    assert 0 <= result.pop("timing_ms")["finish_ms"] <= 120000
    assert result == {"complete": False, "stage": "finish", "reason": "finish_timeout"}


def test_dns_observation_only_accepts_complete_bounded_counters() -> None:
    value = {
        "complete": True,
        "packet_loss": False,
        "counts": {
            "tunnel": {"outbound": 1, "inbound": 0},
            "uplink": {"outbound": 1, "inbound": 0},
        },
        "private_untrusted_field": "synthetic secret",
    }
    result = sanitize_dns_observation(value)
    assert result == {
        "complete": True,
        "packet_loss": False,
        "counts": value["counts"],
    }
    assert "synthetic secret" not in repr(result)
    for bad in (
        {**value, "packet_loss": True},
        {**value, "complete": False},
        {**value, "counts": {"tunnel": {"inbound": 0, "outbound": 64}}},
    ):
        with pytest.raises(ValueError, match="^dns_observation_invalid$"):
            sanitize_dns_observation(bad)


def test_egress_suite_is_a_distinct_bounded_diagnostic() -> None:
    lifecycle, phase, facts = i028_suite_config("Egress")
    assert not lifecycle
    assert phase == "egress"
    assert facts == {
        "real_account_refresh_after_android_process_restart",
        "included_probe_real_staging_ipv4_and_udp_dns",
        "included_ipv6_literal_blocked",
    }
    assert "sustained_real_renewals_over_three_minutes" in i028_suite_config("Main")[2]
    with pytest.raises(ValueError, match="^i028_suite_invalid$"):
        i028_suite_config("unknown")


def test_install_relaunch_preserves_selected_suite() -> None:
    script = (ROOT / "scripts/run_i028_staging.ps1").read_text(encoding="utf-8")
    assert "'-Action', 'Install', '-Suite', $Suite, '-RunHere'" in script


def test_egress_hold_marks_stale_handshake_only_with_recent_inbound_progress() -> None:
    def sample(elapsed: int, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "elapsed_seconds": elapsed,
            "client_phase": "connected",
            "client_has_error": False,
            "native_up": True,
            "native_network": True,
            "handshake_age": 75,
            "remaining_ms": 30000,
            "received_counter_available": True,
            "inbound_progress_recent": True,
            "renewal_acknowledged": True,
            "traffic_counter_available": True,
            "traffic_renewal_requested": True,
            "renewal_pending": False,
        }
        value.update(overrides)
        return value

    def payload(observations: list[dict[str, object]]) -> dict[str, object]:
        return {
            "fixed_probe_attempted": True,
            "fixed_probe_succeeded": True,
            "observations": observations,
        }

    passed = sanitize_egress_hold_observations(payload([sample(5, handshake_age=58), sample(10)]))
    assert passed["stale_handshake_connected_with_recent_inbound"] is True
    assert passed["sample_count"] == 2
    assert passed["observations"][0]["traffic_counter_available"] is True
    assert passed["observations"][0]["traffic_renewal_requested"] is True
    assert passed["observations"][0]["renewal_pending"] is False

    for override in (
        {"inbound_progress_recent": False},
        {"client_phase": "reconnecting"},
        {"client_has_error": True},
        {"native_up": False},
        {"native_network": False},
        {"remaining_ms": 0},
        {
            "received_counter_available": False,
            "inbound_progress_recent": False,
        },
    ):
        result = sanitize_egress_hold_observations(payload([sample(10, **override)]))
        assert result["stale_handshake_connected_with_recent_inbound"] is False

    fresh = sanitize_egress_hold_observations(payload([sample(10, handshake_age=60)]))
    assert fresh["stale_handshake_connected_with_recent_inbound"] is False


def test_egress_hold_observation_rejects_unbounded_or_malformed_samples() -> None:
    base = {
        "elapsed_seconds": 5,
        "client_phase": "connected",
        "client_has_error": False,
        "native_up": True,
        "native_network": True,
        "handshake_age": 61,
        "remaining_ms": 30000,
        "received_counter_available": True,
        "inbound_progress_recent": True,
        "renewal_acknowledged": True,
    }

    def payload(observations: list[dict[str, object]]) -> dict[str, object]:
        return {
            "fixed_probe_attempted": True,
            "fixed_probe_succeeded": False,
            "observations": observations,
        }

    bad_values = (
        {},
        payload([]),
        payload([base, {**base, "elapsed_seconds": 5}]),
        payload([{**base, "handshake_age": True}]),
        payload([{**base, "traffic_counter_available": 1}]),
        payload([{**base, "traffic_renewal_requested": 1}]),
        payload([{**base, "renewal_pending": "no"}]),
        payload(
            [
                {
                    **base,
                    "inbound_progress_recent": True,
                    "received_counter_available": False,
                }
            ]
        ),
        payload([base] * 33),
        payload([{**base, "private": "must not be retained"}]),
        {"fixed_probe_attempted": False, "fixed_probe_succeeded": True, "observations": [base]},
    )
    for value in bad_values:
        with pytest.raises(ValueError, match="^egress_hold_observation_invalid$"):
            sanitize_egress_hold_observations(value)


def test_recovery_node_status_keeps_only_bounded_aggregate_fields() -> None:
    result = sanitize_recovery_node_status(
        {
            "prepared": True,
            "agent_active": True,
            "live_slots": [1],
            "peer_counts": [1, 0],
            "native_transfer_bytes": [
                {"slot": 1, "received": "42", "sent": "51", "peer": "private"},
                {"slot": 2, "received": "0", "sent": "0"},
            ],
            "byte_quota_exhausted": [
                {
                    "slot": 1,
                    "exhausted": True,
                    "remaining_bytes": 0,
                    "private": "secret",
                }
            ],
            "private": "synthetic private value",
        }
    )
    assert result == {
        "available": True,
        "agent_active": True,
        "live_slots": [1],
        "peer_counts": [1, 0],
        "native_transfer_bytes": [
            {"slot": 1, "received": "42", "sent": "51"},
            {"slot": 2, "received": "0", "sent": "0"},
        ],
        "byte_quota_exhausted": [{"slot": 1, "exhausted": True, "remaining_bytes": 0}],
    }
    assert "private" not in repr(result)


def test_quota_snapshot_does_not_trust_invalid_or_duplicate_rows() -> None:
    base = {
        "prepared": True,
        "agent_active": True,
        "live_slots": [1],
        "peer_counts": [1, 0],
        "native_transfer_bytes": [
            {"slot": 1, "received": "0", "sent": "0"},
            {"slot": 2, "received": "0", "sent": "0"},
        ],
    }
    for invalid in (
        [{"slot": 1, "exhausted": "private"}],
        [{"slot": True, "exhausted": True}],
        [{"slot": 1, "exhausted": True}, {"slot": 1, "exhausted": True}],
    ):
        result = sanitize_recovery_node_status({**base, "byte_quota_exhausted": invalid})
        assert result["available"] is True
        assert "byte_quota_exhausted" not in result
    assert sanitize_recovery_node_status(
        {**base, "byte_quota_exhausted": [{"slot": 1, "exhausted": None}]}
    )["byte_quota_exhausted"] == [{"slot": 1, "exhausted": None}]


def test_recovery_connection_status_keeps_only_bounded_generation_and_budget() -> None:
    value = {
        "available": True,
        "state": "active",
        "generation": 7,
        "budget_bytes": 1048576,
        "lease_remaining_seconds": 78,
        "connection_id": "private",
    }
    assert sanitize_recovery_connection_status(value) == {
        "available": True,
        "state": "active",
        "generation": 7,
        "budget_bytes": 1048576,
        "lease_remaining_seconds": 78,
    }
    assert sanitize_recovery_connection_status({**value, "generation": True}) == {
        "available": False
    }
    assert sanitize_recovery_connection_status({**value, "lease_remaining_seconds": 91}) == {
        "available": False
    }


def test_egress_renewal_status_is_bounded_and_requires_active_ack() -> None:
    assert sanitize_egress_renewal_status(
        {"available": True, "generation": 3, "state": "pending", "id": "secret"}
    ) == {
        "available": True,
        "renewal_seen": True,
        "acknowledged": False,
        "generation": 3,
        "state": "pending",
    }
    assert sanitize_egress_renewal_status(
        {"available": True, "generation": 3, "state": "active", "id": "secret"}
    ) == {
        "available": True,
        "renewal_seen": True,
        "acknowledged": True,
        "generation": 3,
        "state": "active",
    }
    assert sanitize_egress_renewal_status(
        {"available": True, "generation": True, "state": "active"}
    ) == {"available": False}


def test_recovery_connection_snapshot_omits_identifiers_and_bounds_lease() -> None:
    connection = SimpleNamespace(
        user_id=uuid4(),
        window_id=uuid4(),
        state="active",
        generation=4,
        created_at=datetime.now(UTC),
    )
    hold = SimpleNamespace(
        not_after=datetime.now(UTC) + timedelta(seconds=80),
        amount_bytes=1048576,
    )

    class SessionStub:
        def scalar(self, _statement):
            return connection

        def get(self, _model, _key):
            return hold

    result = recovery_connection_snapshot(SessionStub(), connection.user_id)
    assert result["available"] is True
    assert result["state"] == "active"
    assert result["generation"] == 4
    assert result["budget_bytes"] == 1048576
    assert 0 <= result["lease_remaining_seconds"] <= 80
    assert "window_id" not in result and "user_id" not in result


def test_sustained_window_observations_are_sanitized_and_bounded() -> None:
    report: dict = {}
    native = {"up": True, "network": True, "remaining_ms": 65000, "private": "secret"}
    connection = {
        "available": True,
        "state": "active",
        "generation": 4,
        "budget_bytes": 1048576,
        "lease_remaining_seconds": 65,
        "connection_id": "private",
    }
    for _ in range(50):
        append_sustained_window_observation(report, native, connection)
    assert len(report["sustained_window_observations"]) == 48
    assert report["sustained_window_observations"][0] == {
        "native": {"up": True, "network": True, "remaining_ms": 65000},
        "connection": {
            "available": True,
            "state": "active",
            "generation": 4,
            "budget_bytes": 1048576,
            "lease_remaining_seconds": 65,
        },
    }
    assert "private" not in repr(report) and "secret" not in repr(report)
    append_sustained_window_observation(
        report, {"up": True, "network": True, "remaining_ms": "invalid"}, connection
    )
    assert len(report["sustained_window_observations"]) == 48


@pytest.mark.parametrize("value", [None, {}, {"prepared": True, "live_slots": [[1]]}])
def test_recovery_node_status_rejects_invalid_data(value: object) -> None:
    assert sanitize_recovery_node_status(value) == {"available": False}


def test_probe_preserves_false_flags_and_bounded_categories() -> None:
    result = parse_probe(
        "INSTRUMENTATION_RESULT: probe_complete=true\r\n"
        "INSTRUMENTATION_RESULT: ipv4_available=false\r\n"
        "INSTRUMENTATION_RESULT: ipv6_failure=route\r\n"
        "INSTRUMENTATION_RESULT: ipv6_literal_connected=false\r\n"
        "INSTRUMENTATION_RESULT: ipv6_literal_failure=route\r\n"
        "INSTRUMENTATION_RESULT: ipv6_literal_connect_attempted=true\r\n"
        "INSTRUMENTATION_RESULT: ipv6_literal_egress_port_control_connected=true\r\n"
        "INSTRUMENTATION_RESULT: ipv6_literal_egress_port_control_failure=none\r\n"
        "INSTRUMENTATION_RESULT: dns_failure=permission\r\n"
        "INSTRUMENTATION_RESULT: ipv4_failure=http_503\r\n"
    )
    assert result == {
        "probe_complete": True,
        "ipv4_available": False,
        "ipv6_failure": "route",
        "ipv6_literal_connected": False,
        "ipv6_literal_failure": "route",
        "ipv6_literal_connect_attempted": True,
        "ipv6_literal_egress_port_control_connected": True,
        "ipv6_literal_egress_port_control_failure": "none",
        "dns_failure": "permission",
        "ipv4_failure": "http_503",
    }


def test_probe_drops_unknown_fields_and_unbounded_error_data() -> None:
    result = parse_probe(
        "INSTRUMENTATION_RESULT: probe_complete=true\n"
        "INSTRUMENTATION_RESULT: unrecognized_private_field=true\n"
        "INSTRUMENTATION_RESULT: dns_failure=TEST SYNTHETIC PRIVATE DIAGNOSTIC\n"
        "INSTRUMENTATION_RESULT: ipv4_available=trueTRAILING_INVALID\n"
    )
    assert result == {"probe_complete": True}


def test_egress_acceptance_requires_same_port_baseline_and_connect_attempt() -> None:
    dart = (ROOT / "apps/client/integration_test/i028_live_test.dart").read_text(encoding="utf-8")
    assert "baseline['ipv6_literal_egress_port_control_connected'] == true" in dart
    assert "included['ipv6_literal_connect_attempted'] == true" in dart
    assert "included['ipv6_literal_failure'] == 'io'" in dart


def test_failed_ipv4_followup_keeps_only_fixed_network_diagnostics() -> None:
    result = parse_probe(
        "INSTRUMENTATION_RESULT: probe_complete=true\n"
        "INSTRUMENTATION_RESULT: ipv4_failure=timeout\n"
        "INSTRUMENTATION_RESULT: fixed_udp_dns_response=false\n"
        "INSTRUMENTATION_RESULT: dns_failure=timeout\n"
        "INSTRUMENTATION_RESULT: active_network_is_vpn=true\n"
        "INSTRUMENTATION_RESULT: vpn_dns_configured=true\n"
        "INSTRUMENTATION_RESULT: network_name=synthetic-private-value\n"
    )
    assert result == {
        "probe_complete": True,
        "ipv4_failure": "timeout",
        "fixed_udp_dns_response": False,
        "dns_failure": "timeout",
        "active_network_is_vpn": True,
        "vpn_dns_configured": True,
    }


def test_fixed_path_results_allow_only_fixed_categories() -> None:
    result = parse_probe(
        "INSTRUMENTATION_RESULT: probe_complete=true\n"
        "INSTRUMENTATION_RESULT: fixed_resolver_result=timeout\n"
        "INSTRUMENTATION_RESULT: fixed_tcp_result=ok\n"
        "INSTRUMENTATION_RESULT: fixed_tls_result=tls\n"
    )
    assert result == {
        "probe_complete": True,
        "fixed_resolver_result": "timeout",
        "fixed_tcp_result": "ok",
        "fixed_tls_result": "tls",
    }
    assert parse_probe(
        "INSTRUMENTATION_RESULT: probe_complete=true\n"
        "INSTRUMENTATION_RESULT: fixed_resolver_result=synthetic-private-value\n"
    ) == {"probe_complete": True}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "INSTRUMENTATION_RESULT: probe_complete=false",
        "INSTRUMENTATION_RESULT: probe_complete=trueTRAILING_INVALID",
    ],
)
def test_probe_requires_exact_completion(text: str) -> None:
    with pytest.raises(RuntimeError, match="^i028_probe_incomplete$"):
        parse_probe(text)


def test_probe_runner_summary_is_bounded_and_drops_raw_instrumentation_text() -> None:
    summary = summarize_probe_runner(
        "INSTRUMENTATION_RESULT: private_data=synthetic-secret\nINSTRUMENTATION_ABORTED\n",
        0,
    )

    assert summary == {
        "adb_exit_code": 0,
        "instrumentation_result_line_count": 1,
        "completion_marker_seen": False,
        "aborted_marker_seen": True,
        "last_stage": None,
        "category": "instrumentation_aborted",
    }
    assert "synthetic" not in repr(summary)


@pytest.mark.parametrize(
    ("output", "exit_code", "category"),
    [
        ("INSTRUMENTATION_RESULT: probe_complete=true\n", 0, "completed"),
        ("", 1, "adb_nonzero"),
        ("", 0, "completion_marker_missing"),
    ],
)
def test_probe_runner_summary_classifies_only_fixed_outcomes(
    output: str, exit_code: int, category: str
) -> None:
    assert summarize_probe_runner(output, exit_code)["category"] == category


def test_timeout_summary_keeps_only_allowlisted_stage_from_partial_output() -> None:
    error = subprocess.TimeoutExpired(
        ["adb", "shell", "am", "instrument"],
        timeout=45,
        output=(
            b"INSTRUMENTATION_STATUS: i028_stage=ipv4_primary_started\n"
            b"INSTRUMENTATION_STATUS: private_data=do-not-retain\n"
        ),
        stderr=b"private stderr text",
    )
    partial = bounded_probe_partial_output(error)
    summary = summarize_probe_runner(partial, None, timed_out=True)

    assert summary == {
        "adb_exit_code": None,
        "instrumentation_result_line_count": 0,
        "completion_marker_seen": False,
        "aborted_marker_seen": False,
        "last_stage": "ipv4_primary_started",
        "category": "adb_timeout",
    }
    assert "do-not-retain" not in repr(summary)
    assert "private stderr text" not in repr(summary)


def test_timeout_summary_drops_unknown_or_unbounded_stage_values() -> None:
    summary = summarize_probe_runner(
        "INSTRUMENTATION_STATUS: i028_stage=private_value_with_address_192_0_2_1\n",
        None,
        timed_out=True,
    )
    assert summary["last_stage"] is None
    assert summary["category"] == "adb_timeout"

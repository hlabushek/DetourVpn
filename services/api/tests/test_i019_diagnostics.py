"""Test-only diagnostic boundary: arbitrary strings/configs must never reach receipts."""

import json
from uuid import uuid4

import pytest
from test_i019_live import (
    MAX_AGENT_EVENTS,
    MAX_EGRESS_PROBE_RECORDS,
    PROBE_RESULTS,
    AgentEventRecorder,
    append_egress_probe,
    independent_egress_result,
    independent_trigger_result,
    sanitized_egress_probe,
    sanitized_phone_failure,
)


def test_agent_event_receipt_uses_bounded_ordinals_without_peer_ids() -> None:
    recorder = AgentEventRecorder()
    first, second = uuid4(), uuid4()
    recorder.record("delivery", first, "remove_peer", 2)
    recorder.record("ack", first, "remove_peer", 2, handler_returned=True, applied=True)
    recorder.record("delivery", second, "add_peer", 1)
    assert [event["peer_ordinal"] for event in recorder.events] == [1, 1, 2]
    assert str(first) not in json.dumps(recorder.events)
    assert str(second) not in json.dumps(recorder.events)
    for _ in range(MAX_AGENT_EVENTS):
        recorder.record("delivery", second, "add_peer", 1)
    assert len(recorder.events) == MAX_AGENT_EVENTS and recorder.truncated
    with pytest.raises(ValueError, match="i019_agent_event_invalid"):
        recorder.record("delivery", first, "raw payload", 1)


def test_literal_trigger_reports_only_one_fixed_category() -> None:
    assert (
        independent_trigger_result(b"INSTRUMENTATION_RESULT: i019_trigger_result=timeout\n")
        == "timeout"
    )
    assert (
        independent_trigger_result(b"INSTRUMENTATION_RESULT: i019_trigger_result=secret_data\n")
        == "unavailable"
    )
    assert independent_trigger_result(b"") == "unavailable"
    assert (
        sanitized_egress_probe(
            {
                "stage": "b_connect_handshake",
                "phase": "response",
                "result": "dns",
                "trigger": "timeout",
            }
        )["trigger"]
        == "timeout"
    )


def test_sanitizer_preserves_all_fixed_probe_results() -> None:
    for category in PROBE_RESULTS | {"unavailable"}:
        record = sanitized_egress_probe(
            {"stage": "b_connect_handshake", "phase": "response", "result": category}
        )
        assert record["result"] == category


def test_egress_probe_receipt_allows_seventeenth_fixed_record_and_stays_bounded() -> None:
    records = [
        {"stage": "authentication", "phase": "response", "result": "other_address"}
        for _ in range(16)
    ]
    assert append_egress_probe(
        records,
        {"stage": "b_connect_egress", "phase": "response", "result": "node_b"},
    ) == {"stage": "b_connect_egress", "phase": "response", "result": "node_b"}
    assert len(records) == 17

    records.extend(
        {"stage": "authentication", "phase": "response", "result": "other_address"}
        for _ in range(MAX_EGRESS_PROBE_RECORDS - len(records))
    )
    try:
        append_egress_probe(
            records,
            {"stage": "b_connect_egress", "phase": "response", "result": "node_b"},
        )
    except ValueError as error:
        assert str(error) == "i019_egress_probe_limit_reached"
    else:
        raise AssertionError("probe receipt cap must remain enforced")


def test_independent_egress_parser_accepts_only_one_fixed_result_line() -> None:
    for category in (
        "node_b",
        "dns",
        "timeout",
        "route",
        "tls",
        "connect",
        "permission",
        "io",
        "http_error",
    ):
        assert (
            independent_egress_result(
                f"INSTRUMENTATION_RESULT: i019_egress_result={category}\r\n".encode()
            )
            == category
        )
    assert (
        independent_egress_result(b"INSTRUMENTATION_RESULT: i019_egress_result=203.0.113.7\n")
        == "instrumentation_missing"
    )
    assert (
        independent_egress_result(b"INSTRUMENTATION_RESULT: i019_egress_result=secret_data\n")
        == "unexpected_result"
    )
    assert (
        independent_egress_result(
            b"INSTRUMENTATION_RESULT: i019_egress_result=node_a\n"
            b"INSTRUMENTATION_RESULT: i019_egress_result=node_b\n"
        )
        == "instrumentation_missing"
    )
    assert independent_egress_result(b"raw address 203.0.113.7") == "instrumentation_missing"
    assert independent_egress_result(b"") == "instrumentation_missing"


def test_egress_probe_drops_addresses_exception_text_and_unbounded_numbers() -> None:
    assert sanitized_egress_probe(
        {
            "stage": "b_connect_egress",
            "phase": "request",
            "result": "socket_error",
            "os_error": 101,
            "http_status": 999,
            "exception": "TEST-SECRET",
            "address": "TEST-PRIVATE-ADDRESS",
        }
    ) == {
        "stage": "b_connect_egress",
        "phase": "request",
        "result": "socket_error",
        "os_error": 101,
    }
    assert sanitized_egress_probe(
        {
            "stage": "TEST-SECRET",
            "phase": {"secret": "TEST-SECRET"},
            "result": "TEST-SECRET",
            "os_error": True,
        }
    ) == {"stage": "other", "phase": "other", "result": "other"}


def test_phone_failure_keeps_only_fixed_vocabulary_and_bounded_metadata() -> None:
    assert sanitized_phone_failure(
        {
            "stage": "b_initial_connect_handshake",
            "phase": "reconnecting",
            "error": "none",
            "nodes_error": "none",
            "native_up": True,
            "native_network": True,
            "has_lease": True,
            "remote_pending": False,
            "handshake_age": -1,
            "remaining_ms": 42000,
            "node_reasons": ["available", "available"],
            "config": "TEST-SENTINEL-DO-NOT-PERSIST",
        }
    ) == {
        "stage": "b_initial_connect_handshake",
        "phase": "reconnecting",
        "error": "none",
        "nodes_error": "none",
        "native_up": True,
        "native_network": True,
        "has_lease": True,
        "remote_pending": False,
        "handshake_age": -1,
        "remaining_ms": 42000,
        "node_reasons": ["available", "available"],
    }


def test_phone_failure_drops_strings_nested_objects_and_numeric_coercion() -> None:
    assert sanitized_phone_failure(
        {
            "stage": "TEST-SECRET",
            "phase": {"config": "TEST-SECRET"},
            "error": ["TEST-SECRET"],
            "nodes_error": "TEST-SECRET",
            "native_up": "TEST-SECRET",
            "handshake_age": True,
            "remaining_ms": 60001,
            "node_reasons": ["TEST-SECRET", {"config": "TEST-SECRET"}],
        }
    ) == {
        "stage": "other",
        "phase": "other",
        "error": "other",
        "nodes_error": "other",
        "node_reasons": ["other", "other"],
    }

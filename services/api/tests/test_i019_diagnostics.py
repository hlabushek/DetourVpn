"""Test-only diagnostic boundary: arbitrary strings/configs must never reach receipts."""

from test_i019_live import sanitized_egress_probe, sanitized_phone_failure


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

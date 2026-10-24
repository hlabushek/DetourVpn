"""Synthetic diagnostics only: never include credentials or arbitrary native fields."""

import pytest
from test_i028_live import parse_probe


def test_probe_preserves_false_flags_and_bounded_categories() -> None:
    result = parse_probe(
        "INSTRUMENTATION_RESULT: probe_complete=true\r\n"
        "INSTRUMENTATION_RESULT: ipv4_available=false\r\n"
        "INSTRUMENTATION_RESULT: dns_failure=permission\r\n"
        "INSTRUMENTATION_RESULT: ipv4_failure=http_503\r\n"
    )
    assert result == {
        "probe_complete": True,
        "ipv4_available": False,
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

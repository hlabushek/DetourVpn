"""TEST ONLY v3 contract checks; no API admission, kernel budget or VPN evidence."""

import base64
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from vpn_api.tunnels.v3_contracts import (
    CAPABILITIES,
    ClosedWindow,
    ContractError,
    WindowGrant,
    validate_capabilities,
    validate_closed_window,
    validate_grant,
)

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def grant(**changes: object) -> WindowGrant:
    return WindowGrant.model_validate(
        {
            "connection_id": uuid4(),
            "window_id": uuid4(),
            "node_id": uuid4(),
            "runtime_id": uuid4(),
            "slot_id": 1,
            "generation": 1,
            "public_key": base64.b64encode(secrets.token_bytes(32)).decode(),
            "issued_at": NOW,
            "not_after": NOW + timedelta(seconds=90),
            "budget_bytes": 1024 * 1024,
            **changes,
        }
    )


def check(value: WindowGrant, previous: WindowGrant | None = None, now: datetime = NOW) -> str:
    binding = previous or value
    return validate_grant(
        value,
        node_id=binding.node_id,
        runtime_id=binding.runtime_id,
        slot_id=binding.slot_id,
        now=now,
        previous=previous,
    )


def renewed(value: WindowGrant, **changes: object) -> WindowGrant:
    return WindowGrant.model_validate(
        {
            **value.model_dump(),
            "window_id": uuid4(),
            "generation": value.generation + 1,
            "issued_at": NOW + timedelta(seconds=30),
            "not_after": NOW + timedelta(seconds=120),
            **changes,
        }
    )


def receipt(value: WindowGrant, **changes: object) -> ClosedWindow:
    return ClosedWindow.model_validate(
        {
            key: field
            for key, field in {
                **value.model_dump(),
                "gate_closed": True,
                "observation": "measured",
                "meter": "kernel-outer-ip.v1",
                "observed_bytes": 0,
                **changes,
            }.items()
            if key not in {"public_key", "issued_at", "not_after", "budget_bytes"}
        }
    )


def test_new_window_renewal_and_exact_replay() -> None:
    first = grant()
    assert check(first) == "new"
    assert check(first, first) == "replay"
    second = renewed(first)
    assert check(second, first, NOW + timedelta(seconds=30)) == "new"
    assert check(second, second, NOW + timedelta(seconds=31)) == "replay"


@pytest.mark.parametrize("budget", [True, 1.0, "65536", 65535, 64 * 1024 * 1024 + 1])
def test_window_budget_is_strict_integer_and_bounded(budget: object) -> None:
    with pytest.raises(ValidationError):
        grant(budget_bytes=budget)


@pytest.mark.parametrize(
    "changes",
    [
        {"issued_at": NOW.replace(tzinfo=None)},
        {"not_after": NOW.replace(tzinfo=None)},
        {"slot_id": 0},
        {"slot_id": 17},
        {"slot_id": True},
        {"generation": 0},
        {"generation": 2**63},
        {"command": "arbitrary-command"},
        {"contract_version": 2},
        {"contract_version": 3.0},
    ],
)
def test_window_rejects_invalid_shape(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        grant(**changes)


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"public_key": "!" * 44}, "invalid_peer_key"),
        ({"not_after": NOW + timedelta(seconds=91)}, "window_duration_invalid"),
        ({"not_after": NOW}, "window_duration_invalid"),
        (
            {"issued_at": NOW + timedelta(seconds=2)},
            "grant_clock_skew",
        ),
        ({"generation": 2}, "initial_generation_required"),
    ],
)
def test_grant_context_fails_closed(changes: dict[str, object], code: str) -> None:
    with pytest.raises(ContractError, match=code):
        check(grant(**changes))


def test_expired_replay_and_naive_clock_do_not_open_access() -> None:
    value = grant()
    with pytest.raises(ContractError, match="window_expired"):
        check(value, value, NOW + timedelta(seconds=90))
    with pytest.raises(ContractError, match="clock_timezone_required"):
        check(value, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_bytes": 2 * 1024 * 1024},
        {"not_after": NOW + timedelta(seconds=89)},
        {"issued_at": NOW + timedelta(seconds=1)},
    ],
)
def test_replay_cannot_change_budget_or_deadline(changes: dict[str, object]) -> None:
    value = grant()
    altered = WindowGrant.model_validate({**value.model_dump(), **changes})
    with pytest.raises(ContractError, match="window_replay_conflict"):
        check(altered, value)


@pytest.mark.parametrize("field", ["node_id", "runtime_id", "connection_id", "window_id"])
def test_receipts_are_bound_to_exact_window_and_runtime(field: str) -> None:
    value = grant()
    with pytest.raises(ContractError, match="receipt_binding_conflict"):
        validate_closed_window(receipt(value, **{field: uuid4()}), value)


def test_renewal_rejects_runtime_change_and_generation_gaps() -> None:
    value = grant()
    now = NOW + timedelta(seconds=30)
    with pytest.raises(ContractError, match="slot_runtime_conflict"):
        check(renewed(value, runtime_id=uuid4()), value, now)
    with pytest.raises(ContractError, match="window_generation_conflict"):
        check(renewed(value, generation=3), value, now)
    with pytest.raises(ContractError, match="connection_binding_conflict"):
        check(renewed(value, connection_id=uuid4()), value, now)
    with pytest.raises(ContractError, match="renewal_too_late"):
        check(renewed(value), value, NOW + timedelta(seconds=85))


def test_final_receipt_distinguishes_zero_from_unknown_and_enforces_closed_gate() -> None:
    value = grant()
    validate_closed_window(receipt(value), value)
    validate_closed_window(receipt(value, observation="unknown", observed_bytes=None), value)
    for changes, code in (
        ({"gate_closed": False}, "final_counter_requires_closed_gate"),
        ({"observation": "unknown"}, "counter_evidence_conflict"),
        ({"observed_bytes": None}, "counter_evidence_conflict"),
        ({"observed_bytes": value.budget_bytes + 1}, "kernel_budget_violation"),
    ):
        with pytest.raises(ContractError, match=code):
            validate_closed_window(receipt(value, **changes), value)


def test_capability_negotiation_rejects_old_missing_and_duplicate_capabilities() -> None:
    values = sorted(CAPABILITIES)
    validate_capabilities(3, values)
    for version, capabilities in (
        (2, values),
        (True, values),
        (3.0, values),
        (3, values[:-1]),
        (3, values + values[:1]),
        (3, values[:-1] + ["peer-lease.v2"]),
    ):
        with pytest.raises(ContractError):
            validate_capabilities(version, capabilities)  # type: ignore[arg-type]

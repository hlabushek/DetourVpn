"""V3 access-window validation only. No admission or native enforcement implied."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt

from vpn_api.tunnels.admission import canonical_key

CAPABILITIES = frozenset(
    {"peer-window.v3", "per-slot-udp-budget.v1", "kernel-deadline.v1", "durable-close-receipt.v1"}
)
MAX_WINDOW_SECONDS = 90
MAX_WINDOW_BYTES = 64 * 1024 * 1024
MIN_WINDOW_BYTES = 64 * 1024
RENEWAL_MARGIN_SECONDS = 5


class ContractError(ValueError):
    """Fixed protocol errors only; no keys, configurations or input echo."""


class FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WindowGrant(FrozenInput):
    contract_version: StrictInt = Field(default=3, ge=3, le=3)
    connection_id: UUID
    window_id: UUID
    node_id: UUID
    runtime_id: UUID
    slot_id: StrictInt = Field(ge=1, le=16)
    generation: StrictInt = Field(ge=1, le=2**63 - 1)
    public_key: str = Field(min_length=44, max_length=44, repr=False)
    issued_at: AwareDatetime
    not_after: AwareDatetime
    budget_bytes: StrictInt = Field(ge=MIN_WINDOW_BYTES, le=MAX_WINDOW_BYTES)


def validate_grant(
    grant: WindowGrant,
    *,
    node_id: UUID,
    runtime_id: UUID,
    slot_id: int,
    now: datetime,
    previous: WindowGrant | None = None,
) -> Literal["new", "replay"]:
    """Caller separately proves funding, ownership and durable revoke/generation state."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("clock_timezone_required")
    try:
        canonical_key(grant.public_key)
    except ValueError:
        raise ContractError("invalid_peer_key") from None
    if (grant.node_id, grant.runtime_id, grant.slot_id) != (node_id, runtime_id, slot_id):
        raise ContractError("slot_runtime_conflict")
    if (
        not timedelta(0)
        < grant.not_after - grant.issued_at
        <= timedelta(seconds=MAX_WINDOW_SECONDS)
    ):
        raise ContractError("window_duration_invalid")
    if grant.issued_at > now + timedelta(seconds=1):
        raise ContractError("grant_clock_skew")
    if grant.not_after <= now:
        raise ContractError("window_expired")
    if previous is None:
        if grant.generation != 1:
            raise ContractError("initial_generation_required")
        return "new"
    if (
        grant.connection_id,
        grant.node_id,
        grant.runtime_id,
        grant.slot_id,
        grant.public_key,
    ) != (
        previous.connection_id,
        previous.node_id,
        previous.runtime_id,
        previous.slot_id,
        previous.public_key,
    ):
        raise ContractError("connection_binding_conflict")
    if grant.window_id == previous.window_id:
        if grant != previous:
            raise ContractError("window_replay_conflict")
        return "replay"
    if grant.generation != previous.generation + 1:
        raise ContractError("window_generation_conflict")
    if previous.not_after <= now + timedelta(seconds=RENEWAL_MARGIN_SECONDS):
        raise ContractError("renewal_too_late")
    if grant.issued_at < previous.issued_at or grant.not_after <= previous.not_after:
        raise ContractError("window_deadline_not_advanced")
    return "new"


class ClosedWindow(FrozenInput):
    contract_version: StrictInt = Field(default=3, ge=3, le=3)
    connection_id: UUID
    window_id: UUID
    node_id: UUID
    runtime_id: UUID
    slot_id: StrictInt = Field(ge=1, le=16)
    generation: StrictInt = Field(ge=1, le=2**63 - 1)
    gate_closed: StrictBool
    observation: Literal["measured", "unknown"]
    meter: Literal["kernel-outer-ip.v1"]
    observed_bytes: StrictInt | None = Field(default=None, ge=0, le=2**63 - 1)


def validate_closed_window(receipt: ClosedWindow, grant: WindowGrant) -> None:
    if (
        receipt.connection_id,
        receipt.window_id,
        receipt.node_id,
        receipt.runtime_id,
        receipt.slot_id,
        receipt.generation,
    ) != (
        grant.connection_id,
        grant.window_id,
        grant.node_id,
        grant.runtime_id,
        grant.slot_id,
        grant.generation,
    ):
        raise ContractError("receipt_binding_conflict")
    if not receipt.gate_closed:
        raise ContractError("final_counter_requires_closed_gate")
    if (receipt.observation == "measured") != (receipt.observed_bytes is not None):
        raise ContractError("counter_evidence_conflict")
    # Counter > budget must be investigated, never silently clipped into compliance.
    if receipt.observed_bytes is not None and receipt.observed_bytes > grant.budget_bytes:
        raise ContractError("kernel_budget_violation")


def validate_capabilities(version: int, capabilities: list[str]) -> None:
    if type(version) is not int or version != 3:
        raise ContractError("peer_protocol_unsupported")
    if len(capabilities) != len(CAPABILITIES) or frozenset(capabilities) != CAPABILITIES:
        raise ContractError("peer_capability_mismatch")

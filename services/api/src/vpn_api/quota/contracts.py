from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_BYTES = 2**63 - 1
BytesAmount = Annotated[int, Field(strict=True, ge=0, le=MAX_BYTES)]


class QuotaError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class LedgerCommand(BaseModel):
    """Trusted internal caller only. Positive amount; registration is a separate path."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    kind: Literal["credit", "debit"]
    amount_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_BYTES)]
    source_id: UUID
    idempotency_key: UUID


class OperationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    operation_id: UUID
    ledger_entry_id: UUID | None
    status: Literal["applied", "rejected"]
    error_code: Literal["insufficient_quota", "quota_capacity_exceeded"] | None
    balance_after_bytes: BytesAmount


class QuotaResponse(BaseModel):
    granted_bytes: BytesAmount
    used_bytes: BytesAmount
    available_bytes: BytesAmount
    reserved_bytes: BytesAmount = 0
    revision: Annotated[int, Field(strict=True, ge=0, le=MAX_BYTES)]
    initial_grant_applied: bool


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sequence: int
    kind: Literal["credit", "debit"]
    delta_bytes: int
    source_type: Literal["registration", "internal", "usage", "tunnel_lease", "reward"]
    source_id: UUID
    idempotency_key: UUID
    balance_after_bytes: BytesAmount
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[LedgerEntryResponse]
    snapshot_revision: int
    next_before_sequence: int | None


class ReconciliationResult(BaseModel):
    consistent: bool
    ledger_entries: int
    receipts: int
    violations: int
    granted_bytes: int
    used_bytes: int
    available_bytes: int

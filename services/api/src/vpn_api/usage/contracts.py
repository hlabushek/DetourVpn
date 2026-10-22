from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from vpn_api.quota.contracts import MAX_BYTES, BytesAmount


class UsageError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class UsageSampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal[1]
    peer_id: UUID
    counter_epoch: UUID
    sequence: Annotated[int, Field(strict=True, ge=1, le=MAX_BYTES)]
    sampled_at: AwareDatetime
    rx_total_bytes: BytesAmount
    tx_total_bytes: BytesAmount
    idempotency_key: UUID

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Schema version must be integer 1")
        return value

    @field_validator("sampled_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("sampled_at", mode="before")
    @classmethod
    def timestamp_type(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("Timestamp requires a timezone-aware datetime or ISO string")
        return value

    @model_validator(mode="after")
    def total_range(self) -> "UsageSampleInput":
        if self.rx_total_bytes + self.tx_total_bytes > MAX_BYTES:
            raise ValueError("Combined counter exceeds BIGINT limit")
        return self


class UsageResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    sample_id: UUID
    status: Literal["charged", "blocked", "no_change", "stale", "rejected"]
    reason: (
        Literal[
            "insufficient_quota",
            "quota_capacity_exceeded",
            "counter_regression",
            "timestamp_regression",
            "outside_epoch",
            "future_sample",
            "stale_conflict",
        ]
        | None
    )
    delta_bytes: BytesAmount
    pending_bytes: BytesAmount
    quota_operation_id: UUID | None


class UsageAudit(BaseModel):
    consistent: bool
    streams: int
    epochs: int
    samples: int
    pending_bytes: int
    violations: int

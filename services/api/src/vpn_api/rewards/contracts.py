from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from vpn_api.quota.contracts import BytesAmount


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    rule_id: UUID
    idempotency_key: UUID


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rule_id: UUID
    reward_type: Literal["daily", "clicker"]
    reward_bytes: BytesAmount
    balance_after_bytes: BytesAmount
    created_at: datetime


class RuleResponse(BaseModel):
    id: UUID
    reward_type: Literal["daily", "clicker"]
    reward_bytes: BytesAmount
    daily_cap_bytes: BytesAmount
    daily_cap_claims: int
    cooldown_seconds: int
    claimed_today: int
    remaining_bytes: BytesAmount
    eligible: bool
    next_claim_at: datetime
    ends_at: datetime


class RulesResponse(BaseModel):
    server_time: datetime
    timezone: Literal["UTC"] = "UTC"
    items: list[RuleResponse]

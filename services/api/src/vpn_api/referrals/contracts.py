from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vpn_api.quota.contracts import BytesAmount


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{24}$")]
    campaign_id: UUID
    idempotency_key: UUID


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    referral_id: UUID
    idempotency_key: UUID


class BindingResponse(BaseModel):
    bound: bool = True


class CodeResponse(BaseModel):
    code: str


class AwardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    referral_id: UUID
    operation_id: UUID
    reward_bytes: BytesAmount
    balance_after_bytes: BytesAmount
    created_at: datetime


class ReferralView(BaseModel):
    id: UUID
    created_at: datetime
    rewarded: bool
    reward_bytes: BytesAmount


class CampaignView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    reward_bytes: BytesAmount
    inviter_cap: int
    ends_at: datetime


class OverviewResponse(BaseModel):
    code: str | None
    bound: bool
    invited: int
    rewarded: int
    campaigns: list[CampaignView]
    items: list[ReferralView]
    next_before_id: UUID | None

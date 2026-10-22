from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vpn_api.usage.contracts import UsageResult, UsageSampleInput


class ProvisionNodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: UUID
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9 _.-]+$")
    region: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$")


class ProvisionNodeResult(BaseModel):
    node_id: UUID
    contract_version: int = 1
    secret: str | None = None
    replayed: bool = False


class AgentUsageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: int = Field(strict=True, ge=1, le=2**31 - 1)
    sample: UsageSampleInput


class AgentUsageResponse(BaseModel):
    contract_version: int = 1
    accepted_at: datetime
    result: UsageResult

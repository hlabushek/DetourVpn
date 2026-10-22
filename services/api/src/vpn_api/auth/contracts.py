from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class StartResponse(BaseModel):
    request_id: UUID
    verifier: str
    bot_url: str
    expires_at: datetime


class VerifyInput(Input):
    request_id: UUID
    verifier: SecretStr
    code: SecretStr

    @field_validator("verifier", "code")
    @classmethod
    def bounded_secret(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Invalid secret length")
        return value


class RefreshInput(Input):
    refresh_token: SecretStr

    @field_validator("refresh_token")
    @classmethod
    def bounded_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) != 43:
            raise ValueError("Invalid token length")
        return value


class TokenResponse(BaseModel):
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - protocol name, not a secret
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class MeResponse(BaseModel):
    id: UUID
    role: str
    telegram_subject: int


class WebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored", "delivery_unknown"]


PositiveId = Annotated[int, Field(strict=True, gt=0, lt=2**63)]


class TelegramUser(BaseModel):
    id: PositiveId
    is_bot: bool = Field(strict=True)


class TelegramChat(BaseModel):
    id: int = Field(strict=True, ge=-(2**63), lt=2**63)
    type: str


class TelegramMessage(BaseModel):
    message_id: PositiveId
    date: int = Field(strict=True, ge=0)
    sender: TelegramUser = Field(alias="from")
    chat: TelegramChat
    text: str = Field(default="", max_length=4096)
    forward_origin: object | None = None
    forward_date: object | None = None
    via_bot: object | None = None
    business_connection_id: object | None = None


class TelegramUpdate(BaseModel):
    update_id: int = Field(strict=True, ge=0, lt=2**63)
    message: TelegramMessage | None = None

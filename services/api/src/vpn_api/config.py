from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vpn_api.db.url import parse_local_database_url


class Settings(BaseSettings):
    """Local settings; database credentials must not appear in repr or validation text."""

    model_config = SettingsConfigDict(
        env_prefix="VPN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["local", "test", "closed"] = "local"
    public_origin: str = ""
    enable_api_docs: bool = False
    enable_admin: bool = False
    closed_admin_only: bool = False
    admin_encryption_key: SecretStr | None = None
    database_url: SecretStr | None = None
    enable_auth: bool = False
    enable_mini_app: bool = False
    mini_app_static_dir: Path | None = None
    auth_pepper: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str = ""
    telegram_bot_id: int = 0
    initial_quota_bytes: int = Field(default=0, ge=0, le=2**63 - 1)
    tunnel_profile_file: Path | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_closed_environment(self) -> "Settings":
        if self.closed_admin_only and (
            self.environment != "closed" or self.enable_auth or not self.enable_admin
        ):
            raise ValueError(
                "Admin-only setup requires closed environment, admin and disabled auth"
            )
        if self.environment != "closed":
            return self
        from urllib.parse import urlsplit

        origin = urlsplit(self.public_origin)
        if (
            not (self.enable_auth or self.closed_admin_only)
            or self.database_url is None
            or self.enable_api_docs
            or origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
        ):
            raise ValueError(
                "Closed operation requires auth or explicit admin setup, DB, HTTPS and no docs"
            )
        url = parse_local_database_url(self.database_url)
        if url.username != "vpn_api" or not url.password:
            raise ValueError(
                "Closed operation requires the dedicated password-protected runtime role"
            )
        return self

    @field_validator("mini_app_static_dir", mode="before")
    @classmethod
    def validate_static_dir(cls, value: object) -> object:
        # An empty env example must never turn into Path('.') and expose the checkout.
        if value == "":
            return None
        return value

    @field_validator("initial_quota_bytes", mode="before")
    @classmethod
    def validate_initial_quota(cls, value: object) -> int:
        # Env is textual; reject floats, bools, exponents and fractional coercion.
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        if type(value) is not int:
            raise ValueError("Initial quota must be an integer number of bytes")
        return value

    @model_validator(mode="after")
    def validate_auth(self) -> "Settings":
        if self.enable_admin:
            from cryptography.fernet import Fernet

            if self.database_url is None or self.admin_encryption_key is None:
                raise ValueError("Administration requires database and independent encryption key")
            try:
                Fernet(self.admin_encryption_key.get_secret_value().encode("ascii"))
            except (ValueError, UnicodeError):
                raise ValueError("Invalid administration encryption key") from None
        if self.enable_mini_app and not self.enable_auth:
            raise ValueError("Mini App requires authentication")
        if not self.enable_auth:
            return self
        import re

        if self.database_url is None:
            raise ValueError("Authentication requires a local database")
        for value in (self.auth_pepper, self.telegram_webhook_secret):
            if value is None or not re.fullmatch(
                r"[A-Za-z0-9_-]{43,128}", value.get_secret_value()
            ):
                raise ValueError("Authentication secrets require 43-128 URL-safe characters")
        token = self.telegram_bot_token
        if (
            self.telegram_bot_id <= 0
            or token is None
            or not re.fullmatch(
                str(self.telegram_bot_id) + r":[A-Za-z0-9_-]{30,128}", token.get_secret_value()
            )
            or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]", self.telegram_bot_username
            )
        ):
            raise ValueError("Authentication requires a matching Telegram bot configuration")
        return self

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        parse_local_database_url(value)
        return value

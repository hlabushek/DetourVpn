"""Latest-release manifest for the in-app updater (ADR-107).

One manifest describes the single latest APK. It is a static file on the server, written by the
release script; the API only validates and serves it. A missing or invalid file is "unavailable",
never a partial or guessed manifest.
"""

import json
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_MANIFEST_BYTES = 16 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}")


class AppUpdateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_code: Annotated[int, Field(ge=1, le=2_147_483_647)]
    version_name: str
    min_supported_version_code: Annotated[int, Field(ge=1, le=2_147_483_647)]
    apk_url: Annotated[str, Field(min_length=12, max_length=512)]
    apk_sha256: str
    apk_size_bytes: Annotated[int, Field(ge=1, le=512 * 1024 * 1024)]
    release_notes_ru: Annotated[str, Field(max_length=2000)]

    @field_validator("version_name")
    @classmethod
    def _version_name(cls, value: str) -> str:
        if not _VERSION_NAME.fullmatch(value):
            raise ValueError("version_name_invalid")
        return value

    @field_validator("apk_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://") or any(ch.isspace() for ch in value):
            raise ValueError("apk_url_invalid")
        return value

    @field_validator("apk_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("apk_sha256_invalid")
        return value

    @model_validator(mode="after")
    def _minimum_not_above_latest(self) -> "AppUpdateManifest":
        if self.min_supported_version_code > self.version_code:
            raise ValueError("min_supported_above_latest")
        return self


def load_manifest(path: Path | None) -> AppUpdateManifest | None:
    """Returns the manifest, or None when it is not configured, missing, oversized or invalid."""
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AppUpdateManifest.model_validate(raw)
    except (OSError, ValueError):
        # pydantic's ValidationError is a ValueError; details are not echoed to clients.
        return None

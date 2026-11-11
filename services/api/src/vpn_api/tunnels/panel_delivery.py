"""Panel servers (3x-ui / Remnawave, VLESS + Reality) for devices (ADR-115).

The list carries no link content. The profile (it holds the shared panel client UUID, a
credential) is decrypted only for an authenticated, bound Windows device and never logged.
"""

from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from vpn_api.auth.security import AuthError, Principal
from vpn_api.config import Settings
from vpn_api.db.models import PanelServerSource
from vpn_api.panel_sources import PanelLinkError, parse_vless_reality
from vpn_api.tunnels.admission import bound_device

MAX_PANEL_ROWS = 20
# Platforms whose engine can run VLESS + Reality (Windows engine has `with_utls`, ADR-112).
VLESS_PLATFORMS = frozenset({"windows"})


class PanelProfileView(BaseModel):
    """Exactly the fields `CreateVlessEngineConfig` needs; each one is re-checked natively."""

    model_config = ConfigDict(hide_input_in_errors=True)
    id: UUID
    protocol: Literal["vless_reality"] = "vless_reality"
    server: str = Field(repr=False)
    port: int
    uuid: str = Field(repr=False)
    flow: str
    sni: str = Field(repr=False)
    public_key: str = Field(repr=False)
    short_id: str = Field(repr=False)
    fingerprint: str


def enabled(settings: Settings) -> bool:
    return settings.deliver_panel_servers and settings.admin_encryption_key is not None


def catalog_rows(session: Session) -> list[PanelServerSource]:
    return list(
        session.scalars(
            select(PanelServerSource)
            .order_by(PanelServerSource.created_at, PanelServerSource.id)
            .limit(MAX_PANEL_ROWS)
        )
    )


def profile(
    session: Session, settings: Settings, principal: Principal, server_id: UUID
) -> PanelProfileView:
    if not enabled(settings) or settings.admin_encryption_key is None:
        raise AuthError("panel_servers_unavailable", 503)
    if principal.device_id is None:
        raise AuthError("session_device_mismatch", 409)
    device = bound_device(session, principal, principal.device_id)
    if device.platform not in VLESS_PLATFORMS:
        raise AuthError("panel_platform_unsupported", 409)
    row = session.get(PanelServerSource, server_id)
    if row is None or row.protocol != "vless_reality":
        raise AuthError("not_found", 404)
    cipher = Fernet(settings.admin_encryption_key.get_secret_value().encode("ascii"))
    try:
        link = parse_vless_reality(
            cipher.decrypt(row.link_ciphertext.encode("ascii")).decode("ascii")
        )
    except (InvalidToken, UnicodeDecodeError, PanelLinkError):
        # A rotated admin key or a corrupted row: the server is unusable until re-imported.
        raise AuthError("panel_server_unreadable", 503) from None
    return PanelProfileView(
        id=row.id,
        server=link.host,
        port=link.port,
        uuid=link.uuid,
        flow=link.flow,
        sni=link.sni,
        public_key=link.public_key,
        short_id=link.short_id,
        fingerprint=link.fingerprint,
    )

"""Per-device traffic attribution and cap check (ADR-093).

The account balance stays the only billing authority. These figures only attribute already
measured/charged bytes to a device: measured v3 window receipts plus charged v1 usage epochs.
Unknown or unmeasured windows count as zero, so a value is a lower bound, never billing.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from vpn_api.db.models import (
    PeerConnection,
    PeerWindowHold,
    PeerWindowReceipt,
    UsageEpoch,
    UsageStream,
    VPNNode,
)


@dataclass(frozen=True)
class DeviceLastSeen:
    connected_at: datetime
    country_code: str | None


def used_bytes(
    session: Session, device_ids: Collection[UUID], since: datetime | None = None
) -> dict[UUID, int]:
    """Attributed bytes per device, optionally only usage recorded at or after `since`."""
    totals: dict[UUID, int] = dict.fromkeys(device_ids, 0)
    if not totals:
        return totals
    windows = (
        select(
            PeerConnection.device_id, func.coalesce(func.sum(PeerWindowReceipt.observed_bytes), 0)
        )
        .join(PeerWindowHold, PeerWindowHold.id == PeerWindowReceipt.window_id)
        .join(PeerConnection, PeerConnection.id == PeerWindowHold.connection_id)
        .where(
            PeerConnection.device_id.in_(totals),
            PeerWindowReceipt.observation == "measured",
        )
        .group_by(PeerConnection.device_id)
    )
    epochs = (
        select(
            UsageStream.device_id,
            func.coalesce(func.sum(UsageEpoch.charged_rx + UsageEpoch.charged_tx), 0),
        )
        .join(UsageEpoch, UsageEpoch.stream_id == UsageStream.id)
        .where(UsageStream.device_id.in_(totals))
        .group_by(UsageStream.device_id)
    )
    if since is not None:
        windows = windows.where(PeerWindowReceipt.created_at >= since)
        epochs = epochs.where(UsageEpoch.created_at >= since)
    for statement in (windows, epochs):
        for device_id, amount in session.execute(statement):
            totals[device_id] += int(amount or 0)
    return totals


def cap_reached(session: Session, device_id: UUID, cap_bytes: int | None) -> bool:
    """True once attributed usage has reached the device's optional cap."""
    if cap_bytes is None:
        return False
    return used_bytes(session, [device_id])[device_id] >= cap_bytes


def last_seen(session: Session, device_ids: Collection[UUID]) -> dict[UUID, DeviceLastSeen]:
    """Most recent v3 connection per device and the country of its node, when known."""
    if not device_ids:
        return {}
    rows = session.execute(
        select(PeerConnection.device_id, PeerConnection.created_at, VPNNode.country_code)
        .join(VPNNode, VPNNode.id == PeerConnection.node_id)
        .where(PeerConnection.device_id.in_(device_ids))
        .order_by(PeerConnection.device_id, PeerConnection.created_at.desc())
        .ext(distinct_on(PeerConnection.device_id))
    )
    return {device_id: DeviceLastSeen(at, country) for device_id, at, country in rows}

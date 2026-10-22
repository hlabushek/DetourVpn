"""Operator-configured bounded nodes; availability is not a connectivity probe."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from vpn_api.auth.routes import Access, Service
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import Tunnel, VPNNode
from vpn_api.schemas import ErrorResponse
from vpn_api.tunnels.admission import NodeProfile, profiles

NodeReason = Literal["available", "disabled", "heartbeat_stale", "runtime_mismatch", "capacity"]
HEARTBEAT_SECONDS = 10


def node_reason(session: Session, selected: NodeProfile, now: datetime) -> NodeReason:
    node = session.get(VPNNode, selected.node_id)
    if node is None or node.status != "active":
        return "disabled"
    if node.peer_protocol_version != 2 or node.peer_runtime_id != selected.runtime_id:
        return "runtime_mismatch"
    if node.last_seen_at is None or node.last_seen_at < now - timedelta(seconds=HEARTBEAT_SECONDS):
        return "heartbeat_stale"
    if session.scalar(
        select(Tunnel.id).where(Tunnel.node_id == node.id, Tunnel.state != "revoked")
    ):
        return "capacity"
    return "available"


class NodeView(BaseModel):
    id: UUID
    name: str
    region: str
    reason: NodeReason


class NodeCatalog(BaseModel):
    nodes: list[NodeView]
    checked_at: datetime


router = APIRouter(
    prefix="/api/v1/nodes",
    tags=["nodes"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 429, 503)},
)


@router.get("", response_model=NodeCatalog)
def list_nodes(auth: Service, access: Access) -> NodeCatalog:
    with transaction(auth.engine) as session:
        auth.authenticate(session, access, "tunnels:manage")
        now = database_now(session)
        result = []
        for selected in profiles(auth):
            node = session.get(VPNNode, selected.node_id)
            if node is not None:
                result.append(
                    NodeView(
                        id=node.id,
                        name=node.name,
                        region=node.region,
                        reason=node_reason(session, selected, now),
                    )
                )
        return NodeCatalog(nodes=result, checked_at=now)

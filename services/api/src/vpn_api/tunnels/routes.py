from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from vpn_api.auth.routes import Access, Service, network
from vpn_api.auth.security import AuthError
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import Tunnel
from vpn_api.nodes.routes import engine
from vpn_api.nodes.service import NodeError, NodeRegistry
from vpn_api.schemas import ErrorResponse
from vpn_api.tunnels.admission import KeyChallenge, bound_device, key_challenge, profile
from vpn_api.tunnels.contracts import (
    PeerAckInput,
    PeerAckResponse,
    PeerPollInput,
    PeerPollResponse,
    TunnelInput,
    TunnelResponse,
)
from vpn_api.tunnels.lifecycle import (
    LifecycleAck,
    LifecycleCommands,
    LifecyclePoll,
    apply_ack,
    commands,
    negotiate,
)
from vpn_api.tunnels.service import (
    acknowledge,
    enqueue_revoke,
    owned_tunnel,
    pending_commands,
    recover_expired,
    request_tunnel,
)

router = APIRouter(
    prefix="/api/v1/tunnels",
    tags=["tunnels"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 422, 429, 503)},
)
agent_router = APIRouter(
    prefix="/api/internal/v1/agent/peers",
    tags=["peer-removal-v1"],
    include_in_schema=False,
)


@router.post(
    "",
    status_code=202,
    response_model=TunnelResponse,
    summary="Persist request; admission pending, no configuration issued",
)
def create_tunnel(
    body: TunnelInput, request: Request, auth: Service, access: Access
) -> TunnelResponse:
    auth.rate("device", network(request))
    return request_tunnel(auth, access, body)


@router.get("", response_model=list[TunnelResponse])
def list_tunnels(
    auth: Service,
    access: Access,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(100, ge=1, le=100),
) -> list[TunnelResponse]:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        tunnels = session.scalars(
            select(Tunnel)
            .where(Tunnel.user_id == principal.user_id)
            .order_by(Tunnel.created_at.desc(), Tunnel.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return [TunnelResponse.model_validate(tunnel) for tunnel in tunnels]


@router.get("/{tunnel_id}", response_model=TunnelResponse)
def get_tunnel(tunnel_id: UUID, auth: Service, access: Access) -> TunnelResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        tunnel = owned_tunnel(session, principal.user_id, tunnel_id)
        if tunnel.lease_deadline is not None and tunnel.lease_deadline <= database_now(session):
            enqueue_revoke(session, tunnel)
        return TunnelResponse.model_validate(tunnel)


@router.post("/key-challenge/{device_id}", response_model=KeyChallenge)
def challenge(device_id: UUID, request: Request, auth: Service, access: Access) -> KeyChallenge:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        return key_challenge(session, principal, device_id)


class TunnelConfig(BaseModel):
    tunnel_id: UUID
    expires_at: str
    config: str = Field(repr=False)


@router.get("/{tunnel_id}/config", response_model=TunnelConfig)
def tunnel_config(tunnel_id: UUID, auth: Service, access: Access) -> TunnelConfig:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        tunnel = owned_tunnel(session, principal.user_id, tunnel_id)
        bound_device(session, principal, tunnel.device_id)
        selected = profile(auth, tunnel.node_id) if tunnel.node_id is not None else None
        if (
            selected is None
            or tunnel.node_id != selected.node_id
            or tunnel.runtime_id != selected.runtime_id
            or tunnel.state != "active"
            or tunnel.lease_deadline is None
            or tunnel.lease_deadline <= database_now(session)
        ):
            raise AuthError("tunnel_config_not_ready", 409)
        return TunnelConfig(
            tunnel_id=tunnel.id,
            expires_at=tunnel.lease_deadline.isoformat(),
            config=selected.client_config(),
        )


@router.delete(
    "/{tunnel_id}",
    response_model=TunnelResponse,
    summary="Persist revoke; revoked only if no peer was issued or node acknowledged",
)
def revoke_tunnel(
    tunnel_id: UUID, request: Request, auth: Service, access: Access
) -> TunnelResponse:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        tunnel = owned_tunnel(session, principal.user_id, tunnel_id)
        enqueue_revoke(session, tunnel)
        return TunnelResponse.model_validate(tunnel)


@router.post("/{tunnel_id}/recover", response_model=TunnelResponse)
def recover_tunnel(
    tunnel_id: UUID, request: Request, auth: Service, access: Access
) -> TunnelResponse:
    auth.rate("device", network(request))
    return recover_expired(auth, access, tunnel_id)


@agent_router.post("/poll", response_model=PeerPollResponse)
def poll(
    body: PeerPollInput,
    db: Annotated[Engine, Depends(engine)],
    node_id: Annotated[UUID | None, Header(alias="X-Agent-Node-Id")] = None,
    timestamp: Annotated[str | None, Header(alias="X-Agent-Timestamp")] = None,
    nonce: Annotated[UUID | None, Header(alias="X-Agent-Nonce")] = None,
    credential: Annotated[str | None, Header(alias="X-Agent-Credential")] = None,
) -> PeerPollResponse:
    if body.contract_version != 1:
        raise NodeError("agent_version_unsupported")
    if node_id is None or timestamp is None or nonce is None or credential is None:
        raise NodeError("agent_unauthorized", 401)
    with transaction(db) as session:
        principal = NodeRegistry.verify_and_consume(
            session, node_id=node_id, timestamp=timestamp, nonce=nonce, credential=credential
        )
        return pending_commands(session, principal.node_id, body.limit)


@agent_router.post("/ack", response_model=PeerAckResponse)
def ack(
    body: PeerAckInput,
    db: Annotated[Engine, Depends(engine)],
    node_id: Annotated[UUID | None, Header(alias="X-Agent-Node-Id")] = None,
    timestamp: Annotated[str | None, Header(alias="X-Agent-Timestamp")] = None,
    nonce: Annotated[UUID | None, Header(alias="X-Agent-Nonce")] = None,
    credential: Annotated[str | None, Header(alias="X-Agent-Credential")] = None,
) -> PeerAckResponse:
    if body.contract_version != 1:
        raise NodeError("agent_version_unsupported")
    if node_id is None or timestamp is None or nonce is None or credential is None:
        raise NodeError("agent_unauthorized", 401)
    with transaction(db) as session:
        principal = NodeRegistry.verify_and_consume(
            session, node_id=node_id, timestamp=timestamp, nonce=nonce, credential=credential
        )
        return acknowledge(session, principal.node_id, body)


@agent_router.post("/lifecycle/poll", response_model=LifecycleCommands)
def lifecycle_poll(
    body: LifecyclePoll,
    auth: Service,
    db: Annotated[Engine, Depends(engine)],
    node_id: Annotated[UUID | None, Header(alias="X-Agent-Node-Id")] = None,
    timestamp: Annotated[str | None, Header(alias="X-Agent-Timestamp")] = None,
    nonce: Annotated[UUID | None, Header(alias="X-Agent-Nonce")] = None,
    credential: Annotated[str | None, Header(alias="X-Agent-Credential")] = None,
) -> LifecycleCommands:
    if body.contract_version != 2:
        raise NodeError("agent_version_unsupported")
    selected = profile(auth, node_id) if node_id is not None else None
    if selected is None or node_id != selected.node_id:
        raise NodeError("agent_unavailable", 503)
    if timestamp is None or nonce is None or credential is None:
        raise NodeError("agent_unauthorized", 401)
    with transaction(db) as session:
        principal = NodeRegistry.verify_and_consume(
            session, node_id=node_id, timestamp=timestamp, nonce=nonce, credential=credential
        )
        if body.runtime_id != selected.runtime_id:
            raise NodeError("agent_runtime_mismatch")
        negotiate(session, principal.node_id, body, selected.server_public_key)
        return commands(session, principal.node_id, body.observed_public_keys)


@agent_router.post("/lifecycle/ack")
def lifecycle_ack(
    body: LifecycleAck,
    auth: Service,
    db: Annotated[Engine, Depends(engine)],
    node_id: Annotated[UUID | None, Header(alias="X-Agent-Node-Id")] = None,
    timestamp: Annotated[str | None, Header(alias="X-Agent-Timestamp")] = None,
    nonce: Annotated[UUID | None, Header(alias="X-Agent-Nonce")] = None,
    credential: Annotated[str | None, Header(alias="X-Agent-Credential")] = None,
) -> dict[str, str]:
    if body.contract_version != 2:
        raise NodeError("agent_version_unsupported")
    selected = profile(auth, node_id) if node_id is not None else None
    if selected is None or node_id != selected.node_id:
        raise NodeError("agent_unavailable", 503)
    if (
        timestamp is None
        or nonce is None
        or credential is None
        or body.server_public_key != selected.server_public_key
    ):
        raise NodeError("agent_unauthorized", 401)
    with transaction(db) as session:
        principal = NodeRegistry.verify_and_consume(
            session, node_id=node_id, timestamp=timestamp, nonce=nonce, credential=credential
        )
        return {"state": apply_ack(session, principal.node_id, body)}

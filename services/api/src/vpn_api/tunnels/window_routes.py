"""Explicitly disabled-by-default v3 user and authenticated node adapters."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vpn_api.auth.routes import Access, Service, network
from vpn_api.auth.security import AuthError
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import PeerConnection, SessionFamily, VPNNode
from vpn_api.nodes.service import NodeError, NodeRegistry
from vpn_api.schemas import ErrorResponse
from vpn_api.tunnels import panel_delivery
from vpn_api.tunnels.admission import bound_device, profiles
from vpn_api.tunnels.v3_contracts import Meter, WindowGrant
from vpn_api.tunnels.window_profiles import WindowNode, load_manifest
from vpn_api.tunnels.window_service import (
    EXISTING_HEARTBEAT_SECONDS,
    ConnectionInput,
    EpochStart,
    RenewalInput,
    WindowCommands,
    WindowPoll,
    available_runtime,
    create_connection,
    current_grant,
    grant_authorized,
    issue_renew_token,
    owned_connection,
    poll_windows,
    renew_connection,
    renew_with_token,
    runtime_ready,
    start_epoch,
    token_connection,
)

LATENCY_PROBE_PORT = 51899

router = APIRouter(
    prefix="/api/v1/window-tunnels",
    tags=["window-tunnels"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)
agent_router = APIRouter(prefix="/api/internal/v3/windows", include_in_schema=False)


class ConnectionView(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    id: UUID
    device_id: UUID
    node_id: UUID
    slot_id: int
    state: Literal["pending", "active", "revoke_pending", "closed"]
    window: WindowGrant
    client_config: str | None = Field(default=None, repr=False)
    meter: Meter = "kernel-outer-ip.v1"


class WindowNodeView(BaseModel):
    id: UUID
    name: str
    region: str
    country_code: str | None = None
    city: str | None = None
    slots: int
    occupied: int
    available: bool
    # ADR-102: where the client sends its UDP latency probe; absent for nodes without one.
    probe_host: str | None = None
    probe_port: int | None = None
    # ADR-115: our VPS node (AmneziaWG window) or an imported panel server (VLESS + Reality).
    source: Literal["vps", "panel"] = "vps"
    protocol: Literal["awg", "vless_reality"] = "awg"


class WindowCatalog(BaseModel):
    nodes: list[WindowNodeView]
    checked_at: datetime


def inventory(auth: AuthService) -> list[WindowNode]:
    if not auth.settings.enable_window_tunnels:
        raise AuthError("window_tunnels_unavailable", 503)
    manifest = load_manifest(auth.settings.window_manifest_file)
    legacy = {node.node_id for node in profiles(auth)}
    if legacy & {node.node_id for node in manifest.nodes}:
        raise AuthError("window_manifest_protocol_conflict", 503)
    return manifest.nodes


def selected_node(auth: AuthService, node_id: UUID | None) -> WindowNode:
    selected = next((node for node in inventory(auth) if node.node_id == node_id), None)
    if selected is None:
        raise AuthError("window_node_unavailable", 503)
    return selected


def view(session: Session, selected: WindowNode, connection: PeerConnection) -> ConnectionView:
    grant = current_grant(session, connection)
    config = None
    if connection.state == "active":
        if not grant_authorized(session, connection, grant):
            connection.state = "revoke_pending"
        else:
            try:
                node = available_runtime(session, selected, EXISTING_HEARTBEAT_SECONDS)
            except AuthError:
                node = None
            if node is not None and node.peer_runtime_id == connection.runtime_id:
                config = selected.peer_slots[connection.slot_id - 1].client_config()
    return ConnectionView(
        id=connection.id,
        device_id=connection.device_id,
        node_id=connection.node_id,
        slot_id=connection.slot_id,
        state=connection.state,
        window=grant,
        client_config=config,
        meter="kernel-outer-ip.v1" if selected.pool_peers is None else "kernel-inner-ip.v1",
    )


@router.get("/nodes", response_model=WindowCatalog)
def nodes(
    auth: Service,
    access: Access,
    include: Literal["panel"] | None = Query(None),
) -> WindowCatalog:
    """VPS nodes; panel servers too when asked with `include=panel` and enabled (ADR-115)."""
    configured = inventory(auth)
    with transaction(auth.engine) as session:
        auth.authenticate(session, access, "tunnels:manage")
        result = []
        for selected in configured:
            node = session.get(VPNNode, selected.node_id)
            if node is None:
                continue
            occupied = int(
                session.scalar(
                    select(func.count())
                    .select_from(PeerConnection)
                    .where(
                        PeerConnection.node_id == node.id,
                        PeerConnection.state != "closed",
                    )
                )
                or 0
            )
            try:
                available_runtime(session, selected)
                available = occupied < len(selected.peer_slots)
            except AuthError:
                available = False
            result.append(
                WindowNodeView(
                    id=node.id,
                    name=node.name,
                    region=node.region,
                    country_code=node.country_code,
                    city=node.city,
                    slots=len(selected.peer_slots),
                    probe_host=selected.probe_host,
                    probe_port=LATENCY_PROBE_PORT if selected.probe_host else None,
                    occupied=occupied,
                    available=available,
                )
            )
        # Older clients do not ask for panel rows and would treat them as VPS nodes.
        if include == "panel" and panel_delivery.enabled(auth.settings):
            for row in panel_delivery.catalog_rows(session):
                result.append(
                    WindowNodeView(
                        id=row.id,
                        name=row.label,
                        region="",
                        slots=0,
                        occupied=0,
                        available=True,
                        source="panel",
                        protocol="vless_reality",
                    )
                )
        return WindowCatalog(nodes=result, checked_at=database_now(session))


@router.get("/panel-servers/{server_id}/profile", response_model=panel_delivery.PanelProfileView)
def panel_profile(
    server_id: UUID, request: Request, auth: Service, access: Access
) -> panel_delivery.PanelProfileView:
    """VLESS + Reality profile of one panel server, for a bound Windows device only (ADR-115)."""
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        return panel_delivery.profile(session, auth.settings, principal, server_id)


@router.post("", response_model=ConnectionView, status_code=202)
def create(
    body: ConnectionInput, request: Request, auth: Service, access: Access
) -> ConnectionView:
    auth.rate("device", network(request))
    selected = selected_node(auth, body.node_id)
    with transaction(auth.engine) as session:
        connection = create_connection(session, auth, access, selected, body)
        return view(session, selected, connection)


@router.get("", response_model=list[ConnectionView])
def connections(
    auth: Service, access: Access, limit: int = Query(20, ge=1, le=50)
) -> list[ConnectionView]:
    configured = {node.node_id: node for node in inventory(auth)}
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        family = session.get(SessionFamily, principal.family_id)
        if family is None or family.device_id is None:
            raise AuthError("session_device_mismatch", 409)
        rows = session.scalars(
            select(PeerConnection)
            .where(
                PeerConnection.user_id == principal.user_id,
                PeerConnection.device_id == family.device_id,
            )
            .order_by(PeerConnection.created_at.desc())
            .limit(limit)
        )
        return [
            view(session, configured[row.node_id], row) for row in rows if row.node_id in configured
        ]


@router.get("/{connection_id}", response_model=ConnectionView)
def get(connection_id: UUID, auth: Service, access: Access) -> ConnectionView:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        connection = owned_connection(session, principal.user_id, connection_id)
        bound_device(session, principal, connection.device_id)
        return view(session, selected_node(auth, connection.node_id), connection)


@router.post("/{connection_id}/renew", response_model=ConnectionView, status_code=202)
def renew(
    connection_id: UUID, body: RenewalInput, request: Request, auth: Service, access: Access
) -> ConnectionView:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        connection = owned_connection(session, principal.user_id, connection_id)
        selected = selected_node(auth, connection.node_id)
        hold = renew_connection(session, auth, access, selected, connection_id, body)
        result = view(session, selected, connection)
        # Retried earlier renewals preserve their immutable grant even if a later window exists.
        result.window = WindowGrant.model_validate(hold.grant)
        if hold.id != connection.window_id:
            result.client_config = None
        return result


RenewToken = Annotated[str | None, Header(alias="X-Window-Renew-Token")]


class RenewTokenView(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    renew_token: str = Field(repr=False)


class NativeWindowView(BaseModel):
    """What the native renewer needs; no client configuration or other connections."""

    model_config = ConfigDict(hide_input_in_errors=True)
    id: UUID
    state: Literal["pending", "active", "revoke_pending", "closed"]
    window: WindowGrant


def native_view(
    session: Session, selected: WindowNode, connection: PeerConnection
) -> NativeWindowView:
    full = view(session, selected, connection)
    return NativeWindowView(id=full.id, state=full.state, window=full.window)


@router.post("/{connection_id}/renew-token", response_model=RenewTokenView)
def renew_token(
    connection_id: UUID, request: Request, auth: Service, access: Access
) -> RenewTokenView:
    """ADR-097: rotate the token the OS-level VPN service uses to renew this connection only."""
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        return RenewTokenView(renew_token=issue_renew_token(session, auth, access, connection_id))


@router.get("/{connection_id}/native", response_model=NativeWindowView)
def native_status(
    connection_id: UUID, request: Request, auth: Service, token: RenewToken = None
) -> NativeWindowView:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        connection = token_connection(session, auth, connection_id, token)
        return native_view(session, selected_node(auth, connection.node_id), connection)


@router.post("/{connection_id}/native-renew", response_model=NativeWindowView, status_code=202)
def native_renew(
    connection_id: UUID,
    body: RenewalInput,
    request: Request,
    auth: Service,
    token: RenewToken = None,
) -> NativeWindowView:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        connection = token_connection(session, auth, connection_id, token)
        selected = selected_node(auth, connection.node_id)
        hold = renew_with_token(session, auth, selected, connection, body)
        result = native_view(session, selected, connection)
        result.window = WindowGrant.model_validate(hold.grant)
        return result


@router.delete("/{connection_id}", response_model=ConnectionView, status_code=202)
def revoke(connection_id: UUID, request: Request, auth: Service, access: Access) -> ConnectionView:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        connection = owned_connection(session, principal.user_id, connection_id)
        bound_device(session, principal, connection.device_id)
        selected = selected_node(auth, connection.node_id)
        if connection.state != "closed":
            connection.state = "revoke_pending"
        connection.renew_token_digest = None
        return view(session, selected, connection)


NodeId = Annotated[UUID | None, Header(alias="X-Agent-Node-Id")]
Timestamp = Annotated[str | None, Header(alias="X-Agent-Timestamp")]
Nonce = Annotated[UUID | None, Header(alias="X-Agent-Nonce")]
Credential = Annotated[str | None, Header(alias="X-Agent-Credential")]


def authenticate_node(
    session: Session,
    node_id: UUID | None,
    timestamp: str | None,
    nonce: UUID | None,
    credential: str | None,
) -> None:
    if node_id is None or timestamp is None or nonce is None or credential is None:
        raise NodeError("agent_unauthorized", 401)
    NodeRegistry.verify_and_consume(
        session, node_id=node_id, timestamp=timestamp, nonce=nonce, credential=credential
    )


@agent_router.post("/inventory")
def current_epoch(
    auth: Service,
    node_id: NodeId = None,
    timestamp: Timestamp = None,
    nonce: Nonce = None,
    credential: Credential = None,
) -> dict[str, str | None]:
    selected = selected_node(auth, node_id)
    with transaction(auth.engine) as session:
        authenticate_node(session, node_id, timestamp, nonce, credential)
        node = session.get(VPNNode, selected.node_id)
        return {"runtime_id": str(node.peer_runtime_id) if node and node.peer_runtime_id else None}


@agent_router.post("/start")
def start(
    body: EpochStart,
    auth: Service,
    node_id: NodeId = None,
    timestamp: Timestamp = None,
    nonce: Nonce = None,
    credential: Credential = None,
) -> dict[str, object]:
    selected = selected_node(auth, node_id)
    with transaction(auth.engine) as session:
        authenticate_node(session, node_id, timestamp, nonce, credential)
        runtime_id = start_epoch(session, auth, selected, body)
        return {
            "runtime_id": str(runtime_id),
            "ready": runtime_ready(session, selected.node_id, runtime_id),
        }


@agent_router.post("/poll", response_model=WindowCommands)
def poll(
    body: WindowPoll,
    auth: Service,
    node_id: NodeId = None,
    timestamp: Timestamp = None,
    nonce: Nonce = None,
    credential: Credential = None,
) -> WindowCommands:
    selected = selected_node(auth, node_id)
    with transaction(auth.engine) as session:
        authenticate_node(session, node_id, timestamp, nonce, credential)
        return poll_windows(session, selected, body)

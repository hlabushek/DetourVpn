from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import Engine

from vpn_api.db.connection import transaction
from vpn_api.nodes.contracts import AgentUsageRequest, AgentUsageResponse
from vpn_api.nodes.service import NodeError, NodeRegistry
from vpn_api.schemas import ErrorResponse
from vpn_api.usage.service import UsageService

router = APIRouter(
    prefix="/api/internal/v1/agent",
    tags=["node-agent-v1"],
    responses={code: {"model": ErrorResponse} for code in (401, 409, 413, 422, 503)},
    include_in_schema=False,
)


def engine(request: Request) -> Engine:
    value: Engine | None = request.app.state.node_engine
    if value is None:
        raise NodeError("agent_unavailable", 503)
    return value


@router.post("/usage", response_model=AgentUsageResponse)
def ingest_usage(
    body: AgentUsageRequest,
    request: Request,
    db: Annotated[Engine, Depends(engine)],
    node_id: Annotated[UUID | None, Header(alias="X-Agent-Node-Id")] = None,
    timestamp: Annotated[str | None, Header(alias="X-Agent-Timestamp")] = None,
    nonce: Annotated[UUID | None, Header(alias="X-Agent-Nonce")] = None,
    credential: Annotated[str | None, Header(alias="X-Agent-Credential")] = None,
) -> AgentUsageResponse:
    if body.contract_version != 1:
        raise NodeError("agent_version_unsupported", 409)
    if node_id is None or timestamp is None or nonce is None or credential is None:
        raise NodeError("agent_unauthorized", 401)
    with transaction(db) as session:
        principal = NodeRegistry.verify_and_consume(
            session,
            node_id=node_id,
            timestamp=timestamp,
            nonce=nonce,
            credential=credential,
        )
        result = UsageService(db).ingest_in_transaction(session, principal.node_id, body.sample)
        return AgentUsageResponse(accepted_at=datetime.now(UTC), result=result)

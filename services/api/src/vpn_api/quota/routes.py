from fastapi import APIRouter, Query

from vpn_api.auth.routes import Access, Service
from vpn_api.db.connection import transaction
from vpn_api.quota.contracts import MAX_BYTES, HistoryResponse, QuotaResponse
from vpn_api.quota.service import LedgerService
from vpn_api.schemas import ErrorResponse

router = APIRouter(
    prefix="/api/v1/quota",
    tags=["quota"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 422, 503)},
)


@router.get("", response_model=QuotaResponse)
def quota(auth: Service, access: Access) -> QuotaResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "quota:read")
        return LedgerService(auth.engine).quota(session, principal.user_id)


@router.get("/history", response_model=HistoryResponse)
def history(
    auth: Service,
    access: Access,
    limit: int = Query(50, ge=1, le=100),
    before_sequence: int | None = Query(None, ge=1, le=MAX_BYTES),
    snapshot_revision: int | None = Query(None, ge=0, le=MAX_BYTES),
) -> HistoryResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "quota:read")
        return LedgerService(auth.engine).history(
            session,
            principal.user_id,
            limit=limit,
            before_sequence=before_sequence,
            snapshot_revision=snapshot_revision,
        )

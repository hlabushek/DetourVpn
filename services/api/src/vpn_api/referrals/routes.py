from uuid import UUID

from fastapi import APIRouter

from vpn_api.auth.routes import Access, Service
from vpn_api.db.connection import transaction
from vpn_api.referrals.contracts import (
    AwardResponse,
    BindingResponse,
    ClaimInput,
    CodeResponse,
    OverviewResponse,
    ResolveInput,
)
from vpn_api.referrals.service import ReferralService
from vpn_api.schemas import ErrorResponse

router = APIRouter(
    prefix="/api/v1/referrals",
    tags=["referrals"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)


def rate(auth: Service, access: Access) -> None:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "referrals:manage")
    auth.rate("reward", str(principal.user_id))


@router.get("", response_model=OverviewResponse)
def overview(auth: Service, access: Access, before_id: UUID | None = None) -> OverviewResponse:
    rate(auth, access)
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "referrals:manage")
        return ReferralService.overview(session, principal.user_id, before_id)


@router.post("/code", response_model=CodeResponse)
def code(auth: Service, access: Access) -> CodeResponse:
    rate(auth, access)
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "referrals:manage")
        return ReferralService.code(session, principal.user_id)


@router.post("/resolve", response_model=BindingResponse)
def resolve(body: ResolveInput, auth: Service, access: Access) -> BindingResponse:
    rate(auth, access)
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "referrals:manage")
        return ReferralService.resolve(session, principal.user_id, body)


@router.post("/claim", response_model=AwardResponse)
def claim(body: ClaimInput, auth: Service, access: Access) -> AwardResponse:
    rate(auth, access)
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "referrals:manage")
        return ReferralService(auth.engine).claim(session, principal.user_id, body)

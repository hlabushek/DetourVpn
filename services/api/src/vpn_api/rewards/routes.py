from fastapi import APIRouter

from vpn_api.auth.routes import Access, Service
from vpn_api.db.connection import transaction
from vpn_api.rewards.contracts import ClaimInput, ClaimResponse, RulesResponse
from vpn_api.rewards.service import RewardService
from vpn_api.schemas import ErrorResponse

router = APIRouter(
    prefix="/api/v1/rewards",
    tags=["rewards"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 409, 422, 429, 503)},
)


@router.get("/rules", response_model=RulesResponse)
def rules(auth: Service, access: Access) -> RulesResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "rewards:claim")
    auth.rate("reward", str(principal.user_id))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "rewards:claim")
        return RewardService(auth.engine).rules(session, principal.user_id)


@router.post("/claim", response_model=ClaimResponse)
def claim(body: ClaimInput, auth: Service, access: Access) -> ClaimResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "rewards:claim")
    auth.rate("reward", str(principal.user_id))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "rewards:claim")
        return RewardService(auth.engine).claim(session, principal.user_id, body)

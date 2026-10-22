from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from vpn_api.auth.contracts import (
    MeResponse,
    RefreshInput,
    StartResponse,
    TelegramUpdate,
    TokenResponse,
    VerifyInput,
    WebhookResponse,
)
from vpn_api.auth.security import AuthError
from vpn_api.auth.service import AuthService
from vpn_api.db.connection import transaction
from vpn_api.schemas import ErrorResponse

router = APIRouter(
    prefix="/api/v1",
    tags=["authentication"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 413, 422, 429, 503)},
)
bearer = HTTPBearer(auto_error=False)
webhook_key = APIKeyHeader(name="X-Telegram-Bot-Api-Secret-Token", auto_error=False)


def service(request: Request) -> AuthService:
    result: AuthService | None = request.app.state.auth
    if result is None:
        raise AuthError("auth_unavailable", 503)
    return result


Service = Annotated[AuthService, Depends(service)]


def access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("unauthorized")
    return credentials.credentials


Access = Annotated[str, Depends(access_token)]


def network(request: Request) -> str:
    # Never trust X-Forwarded-For; reverse-proxy trust is a separate deployment gate.
    return request.client.host if request.client is not None else "unknown"


@router.post("/auth/start", response_model=StartResponse)
def start(request: Request, auth: Service) -> StartResponse:
    return auth.start(network(request))


@router.post("/auth/verify", response_model=TokenResponse)
def verify(body: VerifyInput, request: Request, auth: Service) -> TokenResponse:
    return auth.verify(
        body.request_id,
        body.verifier.get_secret_value(),
        body.code.get_secret_value(),
        network(request),
    )


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(body: RefreshInput, request: Request, auth: Service) -> TokenResponse:
    return auth.refresh(body.refresh_token.get_secret_value(), network(request))


@router.get("/me", response_model=MeResponse)
def me(auth: Service, access: Access) -> MeResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "self:read")
        return MeResponse(
            id=principal.user_id, role=principal.role, telegram_subject=principal.telegram_subject
        )


@router.post("/auth/logout", status_code=204)
def logout(auth: Service, access: Access) -> Response:
    auth.logout(access, all_sessions=False)
    return Response(status_code=204)


@router.post("/auth/logout-all", status_code=204)
def logout_all(auth: Service, access: Access) -> Response:
    auth.logout(access, all_sessions=True)
    return Response(status_code=204)


def trusted_webhook(auth: Service, supplied: Annotated[str | None, Depends(webhook_key)]) -> None:
    import hmac

    expected = auth.settings.telegram_webhook_secret
    # Encode arbitrary header input before comparison; compare_digest(str) rejects non-ASCII.
    if expected is None or not hmac.compare_digest(
        (supplied or "").encode(), expected.get_secret_value().encode()
    ):
        raise AuthError("untrusted_webhook", 403)


@router.post(
    "/telegram/webhook", response_model=WebhookResponse, dependencies=[Depends(trusted_webhook)]
)
def webhook(body: TelegramUpdate, request: Request, auth: Service) -> WebhookResponse:
    return auth.webhook(body, network(request))

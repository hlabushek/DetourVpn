from ipaddress import ip_address, ip_network
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


FORWARDED_INVALID = "forwarded-invalid"


def network(request: Request) -> str:
    """Rate-limit subject for the caller's network (ADR-171).

    X-Forwarded-For is trusted only when the direct peer is the configured local proxy
    (`VPN_TRUSTED_PROXY_ADDRESS`), which overwrites the header with its own peer address.
    Otherwise the direct peer is the subject. IPv6 clients are grouped by /64.
    """
    peer = request.client.host if request.client is not None else "unknown"
    app = request.scope.get("app")
    trusted = getattr(getattr(app, "state", None), "trusted_proxy", None)
    try:
        from_proxy = trusted is not None and ip_address(peer) == trusted
    except ValueError:
        from_proxy = False
    if not from_proxy:
        return peer
    values = request.headers.getlist("x-forwarded-for")
    if len(values) != 1 or "," in values[0]:
        # The proxy always sends exactly one address; anything else is a misconfiguration.
        return FORWARDED_INVALID
    try:
        client = ip_address(values[0].strip())
    except ValueError:
        return FORWARDED_INVALID
    if client.version == 6:
        return str(ip_network(f"{client}/64", strict=False))
    return str(client)


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
            id=principal.user_id,
            role=principal.role,
            telegram_subject=0 if principal.guest else principal.telegram_subject,
            guest=principal.guest,
            device_id=principal.device_id,
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

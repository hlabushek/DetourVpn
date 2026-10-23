from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from vpn_api.admin import AdminService
from vpn_api.admin import router as admin_router
from vpn_api.auth.mini_app import router as mini_app_router
from vpn_api.auth.routes import router
from vpn_api.auth.security import AuthError
from vpn_api.auth.service import AuthService
from vpn_api.auth.telegram import Sender, TelegramSender
from vpn_api.body_limit import BodyLimitMiddleware
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine
from vpn_api.devices import router as devices_router
from vpn_api.nodes.catalog import router as nodes_router
from vpn_api.nodes.routes import router as agent_router
from vpn_api.nodes.service import NodeError
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.routes import router as quota_router
from vpn_api.readiness import database_ready
from vpn_api.referrals.routes import router as referrals_router
from vpn_api.rewards.routes import router as rewards_router
from vpn_api.schemas import ErrorDetail, ErrorResponse, HealthResponse
from vpn_api.subscriptions import router as subscriptions_router
from vpn_api.tunnels.routes import agent_router as peer_agent_router
from vpn_api.tunnels.routes import router as tunnels_router


def create_app(
    settings: Settings | None = None,
    *,
    database_engine: Engine | None = None,
    telegram_sender: Sender | None = None,
) -> FastAPI:
    """Local API. Auth is opt-in; liveness never checks dependencies."""
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = database_engine
        app.state.auth = None
        app.state.admin = None
        app.state.node_engine = engine
        if config.database_url is not None and engine is None:
            engine = create_database_engine(config)
            app.state.node_engine = engine
        if config.enable_auth:
            engine = engine if engine is not None else create_database_engine(config)
            if config.telegram_bot_token is None:
                raise ValueError("Authentication requires a bot token")
            sender = (
                telegram_sender
                if telegram_sender is not None
                else TelegramSender(config.telegram_bot_token)
            )
            app.state.auth = AuthService(engine, config, sender)
        if config.enable_admin:
            engine = engine if engine is not None else create_database_engine(config)
            app.state.admin = AdminService(engine, config)
        try:
            yield
        finally:
            if engine is not None and database_engine is None:
                engine.dispose()

    app = FastAPI(
        title="Project VPN Control API",
        version="0.1.0",
        description="Local control plane: Telegram code authentication. No VPN data plane.",
        lifespan=lifespan,
        docs_url="/docs" if config.enable_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.enable_api_docs else None,
        redirect_slashes=False,
    )
    app.state.auth = None
    app.state.admin = None
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(router)
    app.include_router(mini_app_router)
    app.include_router(devices_router)
    app.include_router(quota_router)
    app.include_router(rewards_router)
    app.include_router(referrals_router)
    app.include_router(agent_router)
    app.include_router(nodes_router)
    app.include_router(tunnels_router)
    app.include_router(peer_agent_router)
    app.include_router(subscriptions_router)
    app.include_router(admin_router)
    if config.enable_admin:
        from pathlib import Path

        app.mount(
            "/admin",
            StaticFiles(
                directory=Path(__file__).resolve().parents[4] / "apps/admin/static", html=True
            ),
        )
    if config.enable_mini_app and config.mini_app_static_dir is not None:
        app.mount("/mini-app", StaticFiles(directory=config.mini_app_static_dir, html=True))

    def error_response(request: Request, status: int, code: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=code,
                    message="Request could not be completed.",
                    request_id=request.state.request_id,
                )
            ).model_dump(mode="json"),
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError) -> JSONResponse:
        return error_response(request, exc.status, exc.code)

    @app.exception_handler(QuotaError)
    async def quota_error(request: Request, exc: QuotaError) -> JSONResponse:
        return error_response(request, exc.status, exc.code)

    @app.exception_handler(NodeError)
    async def node_error(request: Request, exc: NodeError) -> JSONResponse:
        return error_response(request, exc.status, exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo inputs, codes, headers or Pydantic context to the caller/log.
        return error_response(request, 422, "invalid_request")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return error_response(request, 503, "storage_unavailable")

    @app.middleware("http")
    async def request_metadata(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith(("/admin", "/api/admin/")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/mini-app/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self' https://telegram.org; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 404:
            code, message = "not_found", "Resource not found."
        elif exc.status_code == 405:
            code, message = "method_not_allowed", "Method not allowed."
        else:
            code, message = "http_error", "Request could not be completed."
        body = ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                request_id=request.state.request_id,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(mode="json"),
            headers=exc.headers,
        )

    @app.get(
        "/api/v1/health",
        response_model=HealthResponse,
        tags=["health"],
        responses={405: {"model": ErrorResponse, "description": "Method not allowed"}},
        summary="Process liveness (not dependency or tunnel readiness)",
    )
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get(
        "/api/v1/ready",
        response_model=HealthResponse,
        tags=["health"],
        responses={503: {"model": ErrorResponse}},
        summary="Control-plane database and migration readiness; excludes VPN and Telegram",
    )
    def ready(request: Request) -> HealthResponse | JSONResponse:
        if not database_ready(
            request.app.state.node_engine, least_privilege=config.environment == "closed"
        ):
            return error_response(request, 503, "dependencies_unavailable")
        return HealthResponse()

    return app

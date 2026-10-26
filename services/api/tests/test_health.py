from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vpn_api.config import Settings
from vpn_api.main import create_app


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPN_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VPN_ENABLE_API_DOCS", raising=False)
    monkeypatch.delenv("VPN_DATABASE_URL", raising=False)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as test_client:
        yield test_client


def test_health_is_public_liveness_only(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4


def test_request_ids_are_generated_and_not_trusted(client: TestClient) -> None:
    first = client.get("/api/v1/health", headers={"X-Request-ID": "untrusted-input"})
    second = client.get("/api/v1/health")
    assert first.headers["x-request-id"] != "untrusted-input"
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_liveness_does_not_connect_to_configured_database() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://unused@127.0.0.1:1/unavailable",
    )
    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/api/v1/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/api/v1/missing", 404, "not_found"),
        ("POST", "/api/v1/health", 405, "method_not_allowed"),
        ("GET", "/api/v1/health/public", 404, "not_found"),
    ],
)
def test_errors_use_stable_contract(
    client: TestClient, method: str, path: str, status: int, code: str
) -> None:
    response = client.request(method, path)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str)
    assert error["request_id"] == response.headers["x-request-id"]
    assert UUID(error["request_id"]).version == 4
    if status == 405:
        assert response.headers["allow"] == "GET"


def test_docs_are_disabled_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_local_docs_can_be_enabled_explicitly() -> None:
    app = create_app(Settings(_env_file=None, environment="test", enable_api_docs=True))
    with TestClient(app) as local_client:
        schema = local_client.get("/openapi.json").json()
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/auth/verify" in schema["paths"]


def test_openapi_documents_mini_app_android_otp_contract() -> None:
    schema = create_app(
        Settings(_env_file=None, environment="test", enable_api_docs=True)
    ).openapi()
    operation = schema["paths"]["/api/v1/auth/mini-app/request-code"]["post"]
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"].endswith("/MiniAppCodeInput")
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/MiniAppCodeResponse")
    assert "request_id" in schema["components"]["schemas"]["MiniAppCodeInput"]["required"]
    assert set(
        schema["components"]["schemas"]["MiniAppCodeResponse"]["properties"]["status"]["enum"]
    ) == {"processed", "delivery_unknown", "rejected"}


def test_openapi_documents_mfa_beta_invite_actions() -> None:
    schema = create_app(
        Settings(_env_file=None, environment="test", enable_api_docs=True)
    ).openapi()
    operation = schema["paths"]["/api/admin/v1/actions"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("/AdminChange")
    contract = schema["components"]["schemas"]["AdminChange"]
    assert "telegram_subject" not in contract.get("required", [])
    assert "grant_beta_invite" in contract["properties"]["operation"]["enum"]
    assert "revoke_beta_invite" in contract["properties"]["operation"]["enum"]


def test_invalid_environment_fails_at_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")


def test_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_ENVIRONMENT", "test")
    monkeypatch.setenv("VPN_ENABLE_API_DOCS", "true")
    settings = Settings(_env_file=None)
    assert settings.environment == "test"
    assert settings.enable_api_docs is True

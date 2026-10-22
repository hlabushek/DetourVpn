"""Storage/auth absence cannot enable tunnel admission or node commands."""

from uuid import uuid4

from fastapi.testclient import TestClient

from vpn_api.config import Settings
from vpn_api.main import create_app


def test_tunnel_routes_fail_closed_without_auth_and_storage() -> None:
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        response = client.post(
            "/api/v1/tunnels", json={"device_id": str(uuid4()), "idempotency_key": str(uuid4())}
        )
        assert (
            response.status_code == 503 and response.json()["error"]["code"] == "auth_unavailable"
        )
        response = client.post(
            "/api/internal/v1/agent/peers/poll",
            json={"contract_version": 1, "capabilities": ["peer-removal.v1"]},
        )
        assert (
            response.status_code == 503 and response.json()["error"]["code"] == "agent_unavailable"
        )
        schema = create_app(Settings(_env_file=None)).openapi()
        assert "/api/v1/tunnels" in schema["paths"]
        assert not any("/api/internal/" in path for path in schema["paths"])

"""Agent API fails closed without a configured storage principal."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from vpn_api.config import Settings
from vpn_api.main import create_app


def test_agent_ingestion_fails_closed_without_database() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app) as client:
        response = client.post(
            "/api/internal/v1/agent/usage",
            json={
                "contract_version": 1,
                "sample": {
                    "schema_version": 1,
                    "peer_id": str(uuid4()),
                    "counter_epoch": str(uuid4()),
                    "sequence": 1,
                    "sampled_at": datetime.now(UTC).isoformat(),
                    "rx_total_bytes": 0,
                    "tx_total_bytes": 0,
                    "idempotency_key": str(uuid4()),
                },
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_unavailable"

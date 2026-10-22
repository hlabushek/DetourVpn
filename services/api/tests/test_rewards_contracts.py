"""Offline negative contract checks; do not prove database/VPN correctness."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from vpn_api.config import Settings
from vpn_api.main import create_app
from vpn_api.rewards.contracts import ClaimInput


@pytest.mark.parametrize("field", ["reward_bytes", "user_id", "reward_type", "created_at", "count"])
def test_client_cannot_supply_reward_authority(field: str) -> None:
    with pytest.raises(ValidationError):
        ClaimInput.model_validate(
            {"rule_id": str(uuid4()), "idempotency_key": str(uuid4()), field: 1}
        )


@pytest.mark.parametrize("field", ["rule_id", "idempotency_key"])
@pytest.mark.parametrize("value", [None, True, 17, "not-a-uuid"])
def test_invalid_claim_identity_is_rejected(field: str, value: object) -> None:
    body: dict[str, object] = {"rule_id": str(uuid4()), "idempotency_key": str(uuid4())}
    body[field] = value
    with pytest.raises(ValidationError):
        ClaimInput.model_validate(body)


def test_reward_routes_require_bearer_and_describe_error_contract() -> None:
    schema = create_app(Settings(_env_file=None, environment="test")).openapi()
    for path, method in (("/api/v1/rewards/rules", "get"), ("/api/v1/rewards/claim", "post")):
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert {"401", "403", "409", "422", "429", "503"} <= operation["responses"].keys()

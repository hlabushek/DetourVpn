"""Beta Bot API token file preparation tests; tokens are synthetic TEST ONLY."""

import pytest
from scripts.beta_store_bot_token import updated_env_text

TOKEN = "123456789:" + "A" * 40
BASE = "\n".join(
    (
        "VPN_ENVIRONMENT=closed",
        "VPN_ENABLE_ADMIN=true",
        "VPN_CLOSED_ADMIN_ONLY=true",
        "VPN_ENABLE_AUTH=false",
        "VPN_ENABLE_MINI_APP=false",
        "VPN_BETA_INVITES_ONLY=false",
        "VPN_DATABASE_URL=postgresql://synthetic:test@127.0.0.1/db",
        "",
    )
)


def test_stores_bot_config_without_enabling_auth() -> None:
    result = updated_env_text(BASE, TOKEN)

    assert f"VPN_TELEGRAM_BOT_TOKEN={TOKEN}" in result
    assert "VPN_TELEGRAM_BOT_ID=123456789" in result
    assert "VPN_TELEGRAM_BOT_USERNAME=uysdgyfudf_bot" in result
    assert "VPN_ENABLE_AUTH=false" in result
    assert "VPN_ENABLE_MINI_APP=false" in result
    assert "VPN_BETA_INVITES_ONLY=false" in result
    assert "VPN_DATABASE_URL=postgresql://synthetic:test@127.0.0.1/db" in result


@pytest.mark.parametrize(
    ("config", "token", "error"),
    [
        (BASE.replace("VPN_ENABLE_AUTH=false", "VPN_ENABLE_AUTH=true"), TOKEN, "closed_admin"),
        (BASE + "VPN_TELEGRAM_BOT_TOKEN=already-present\n", TOKEN, "already_present"),
        (BASE + "VPN_ENABLE_AUTH=false\n", TOKEN, "duplicate_beta_environment_key"),
        (BASE, "not-a-token", "invalid_bot_api_token_format"),
    ],
)
def test_fails_closed(config: str, token: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        updated_env_text(config, token)

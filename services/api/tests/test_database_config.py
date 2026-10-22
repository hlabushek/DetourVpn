from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine


def test_database_is_optional_for_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPN_DATABASE_URL", raising=False)
    assert Settings(_env_file=None).database_url is None
    with pytest.raises(ValueError, match="VPN_DATABASE_URL"):
        create_database_engine(Settings(_env_file=None))


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local.db",
        "postgresql://user@127.0.0.1/db",
        "postgresql+psycopg://user@remote.invalid/db",
        "postgresql+psycopg://user@127.0.0.1/db?hostaddr=192.0.2.1",
        "postgresql+psycopg://user@127.0.0.1/db?service=other",
        "postgresql+psycopg://127.0.0.1/db",
        "postgresql+psycopg://user@127.0.0.1/",
        "postgresql+psycopg://user@127.0.0.1:0/db",
        "postgresql+psycopg://user@127.0.0.1:65536/db",
    ],
)
def test_database_configuration_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


def test_database_url_is_masked_even_when_invalid() -> None:
    secret = uuid4().hex
    valid = f"postgresql+psycopg://user:{secret}@127.0.0.1/test"
    settings = Settings(_env_file=None, database_url=valid)
    assert isinstance(settings.database_url, SecretStr)
    assert secret not in repr(settings)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url=valid + "?hostaddr=192.0.2.1")
    assert secret not in str(error.value)


def test_engine_has_safe_defaults_without_connecting() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user@127.0.0.1/test")
    engine = create_database_engine(settings)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.hide_parameters is True
        assert engine.echo is False
    finally:
        engine.dispose()

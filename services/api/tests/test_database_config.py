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


def test_beta_invites_require_closed_or_test_authenticated_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, beta_invites_only=True)


NEON_LIKE = (
    "postgresql+psycopg://vpn_api:secret@ep-test-1.eu-central-1.aws.neon.tech/vpn"
    "?sslmode=require&channel_binding=require"
)


@pytest.mark.parametrize(
    "url",
    [
        NEON_LIKE,
        "postgresql+psycopg://vpn_api:secret@ep-test-1.eu-central-1.aws.neon.tech/vpn",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?sslmode=verify-full",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?sslmode=verify-ca",
    ],
)
def test_database_configuration_accepts_tls_remote_targets(url: str) -> None:
    settings = Settings(_env_file=None, database_url=url)
    assert settings.database_url is not None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?sslmode=disable",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?sslmode=prefer",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?sslmode=allow",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?hostaddr=192.0.2.1",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?service=other",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?sslrootcert=/tmp/x.pem",
        "postgresql+psycopg://vpn_api:secret@db.example.test/vpn?channel_binding=prefer",
        "postgresql+psycopg://vpn_api:secret@192.0.2.1/vpn?sslmode=require",
        "postgresql+psycopg://vpn_api:secret@localhost.invalid./vpn?sslmode=require",
        "sqlite:///local.db",
        "postgresql://user@127.0.0.1/db",
        "postgresql://user:secret@db.example.test/vpn?sslmode=require",
        "postgresql+psycopg://user@127.0.0.1/db?hostaddr=192.0.2.1",
        "postgresql+psycopg://user@127.0.0.1/db?sslmode=require",
        "postgresql+psycopg://user@127.0.0.1/db?service=other",
        "postgresql+psycopg://127.0.0.1/db",
        "postgresql+psycopg://user@127.0.0.1/",
        "postgresql+psycopg://user@127.0.0.1:0/db",
        "postgresql+psycopg://user@127.0.0.1:65536/db",
        "postgresql+psycopg://user@127.0.0.1/host=remote.invalid dbname=db",
        "postgresql+psycopg://user@127.0.0.1/postgresql://remote.invalid/db",
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


def test_remote_engine_is_lazy_and_keeps_tls_defaults() -> None:
    from vpn_api.db.url import parse_database_url

    url = "postgresql+psycopg://vpn_api:secret@db.example.test/vpn"
    target = parse_database_url(SecretStr(url))
    assert target.is_loopback is False
    assert target.ssl_mode == "verify-full"
    settings = Settings(_env_file=None, database_url=url)
    engine = create_database_engine(settings)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.url.host == "db.example.test"
        assert engine.url.query == {}
    finally:
        engine.dispose()

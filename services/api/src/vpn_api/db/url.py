from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

LOOPBACK_ADDRESSES = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "::1"}


def parse_local_database_url(value: SecretStr) -> URL:
    """Reject unsupported targets and libpq query overrides without echoing credentials."""
    message = "Use a local postgresql+psycopg URL with username/database and no query parameters."
    try:
        url = make_url(value.get_secret_value())
        if (
            url.drivername != "postgresql+psycopg"
            or url.host not in LOOPBACK_ADDRESSES
            or not url.username
            or not url.database
            or url.query
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError(message)
    except (ArgumentError, ValueError, TypeError):
        raise ValueError(message) from None
    return url

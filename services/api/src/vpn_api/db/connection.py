from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from vpn_api.config import Settings
from vpn_api.db.url import LOOPBACK_ADDRESSES, parse_local_database_url


def create_database_engine(settings: Settings) -> Engine:
    """Construct a lazy, local-only engine. The caller owns dispose/lifetime."""
    if settings.database_url is None:
        raise ValueError("Set VPN_DATABASE_URL before running database commands.")
    url = parse_local_database_url(settings.database_url)
    return create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={
            "hostaddr": LOOPBACK_ADDRESSES[str(url.host)],
            "port": url.port or 5432,
            "connect_timeout": 5,
            "options": "-c timezone=UTC -c search_path=public",
            "sslmode": "disable",  # I-004 supports loopback development only.
            "gssencmode": "disable",
        },
    )


@contextmanager
def transaction(engine: Engine) -> Iterator[Session]:
    """Commit exactly one transaction, or roll it back when the caller raises."""
    with Session(engine, expire_on_commit=False) as session, session.begin():
        yield session

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from vpn_api.config import Settings
from vpn_api.db.url import LOOPBACK_ADDRESSES, parse_database_url


def create_database_engine(settings: Settings) -> Engine:
    """Construct a lazy engine. Loopback stays unencrypted (local only); remote hosts need TLS.

    ADR-166: remote hosts are accepted with verified TLS. The CA bundle is the system
    trust store (libpq >= 16 "system"), so managed providers with public certificates work.
    The caller owns dispose/lifetime.
    """
    if settings.database_url is None:
        raise ValueError("Set VPN_DATABASE_URL before running database commands.")
    target = parse_database_url(settings.database_url)
    url = target.url
    common: dict[str, object] = {
        "port": url.port or 5432,
        "connect_timeout": 5,
        "options": "-c timezone=UTC -c search_path=public",
        "gssencmode": "disable",
    }
    if target.is_loopback:
        connect_args = {
            **common,
            "hostaddr": LOOPBACK_ADDRESSES[str(url.host)],
            "sslmode": "disable",  # I-004 supports loopback development only.
        }
    else:
        connect_args = {
            **common,
            "sslmode": target.ssl_mode,
            "sslrootcert": "system",
        }
        if target.channel_binding is not None:
            connect_args["channel_binding"] = target.channel_binding
    return create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args=connect_args,
    )


@contextmanager
def transaction(engine: Engine) -> Iterator[Session]:
    """Commit exactly one transaction, or roll it back when the caller raises."""
    with Session(engine, expire_on_commit=False) as session, session.begin():
        yield session

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from vpn_api.config import Settings
from vpn_api.db.url import LOOPBACK_ADDRESSES, parse_database_url

# Common CA bundle files on Linux distributions. The psycopg wheel bundles its own OpenSSL,
# which does not read the distribution trust store, so sslrootcert="system" fails with
# "certificate verify failed" there (seen on Ubuntu 24.04, 2026-11-22). Pointing libpq at
# the distribution bundle file keeps full verification (verify-full) and works on any libpq.
_SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


def remote_ca_file() -> str:
    """Return the CA bundle file libpq should trust, or "system" when none is found."""
    for path in _SYSTEM_CA_BUNDLES:
        if os.path.isfile(path):
            return path
    return "system"


def create_database_engine(settings: Settings) -> Engine:
    """Construct a lazy engine. Loopback stays unencrypted (local only); remote hosts need TLS.

    ADR-166: remote hosts are accepted with verified TLS (verify-full). The CA bundle is the
    distribution trust store file when present, otherwise libpq's "system" store.
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
            "sslrootcert": remote_ca_file(),
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

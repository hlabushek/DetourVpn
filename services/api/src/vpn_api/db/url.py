import re
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

LOOPBACK_ADDRESSES = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "::1"}

# ADR-166: remote hosts (managed PostgreSQL such as Neon) are accepted only with TLS.
# The allowlist is deliberately short: anything else (hostaddr, service, dbname, options,
# passfile, sslrootcert overrides, ...) is refused, because libpq would honour it.
REMOTE_SSL_MODES = frozenset({"require", "verify-ca", "verify-full"})
REMOTE_DEFAULT_SSL_MODE = "verify-full"
ALLOWED_REMOTE_QUERY_KEYS = frozenset({"sslmode", "channel_binding"})
_DATABASE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_HOSTNAME_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_MESSAGE = (
    "Use a postgresql+psycopg URL with username and database, no hostaddr/service/"
    "options parameters. Remote hosts require TLS (sslmode=require, verify-ca or verify-full)."
)


@dataclass(frozen=True)
class DatabaseTarget:
    url: URL
    is_loopback: bool
    ssl_mode: str
    channel_binding: str | None


def _is_remote_hostname(host: str) -> bool:
    if len(host) > 253 or "." not in host:
        return False
    labels = host.split(".")
    if all(label.isdigit() for label in labels):
        return False  # IP literals are not accepted for remote hosts.
    return all(_HOSTNAME_LABEL.fullmatch(label) for label in labels)


def parse_database_url(value: SecretStr) -> DatabaseTarget:
    """Validate the database URL. Never echoes credentials or the raw value."""
    try:
        url = make_url(value.get_secret_value())
        if (
            url.drivername != "postgresql+psycopg"
            or not url.username
            or not url.database
            or _DATABASE_NAME.fullmatch(url.database) is None
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError(_MESSAGE)
        if url.host in LOOPBACK_ADDRESSES:
            if url.query:
                raise ValueError(_MESSAGE)
            return DatabaseTarget(
                url=url, is_loopback=True, ssl_mode="disable", channel_binding=None
            )
        if not _is_remote_hostname(str(url.host)):
            raise ValueError(_MESSAGE)
        query = dict(url.query)
        if not set(query) <= ALLOWED_REMOTE_QUERY_KEYS:
            raise ValueError(_MESSAGE)
        ssl_mode = query.get("sslmode", REMOTE_DEFAULT_SSL_MODE)
        if ssl_mode not in REMOTE_SSL_MODES:
            raise ValueError(_MESSAGE)
        channel_binding = query.get("channel_binding")
        if channel_binding is not None and channel_binding != "require":
            raise ValueError(_MESSAGE)
        return DatabaseTarget(
            url=url.set(query={}),
            is_loopback=False,
            ssl_mode=ssl_mode,
            channel_binding=channel_binding,
        )
    except (ArgumentError, ValueError, TypeError):
        raise ValueError(_MESSAGE) from None


def parse_local_database_url(value: SecretStr) -> URL:
    """Loopback-only variant kept for the local operations scripts (ADR-011 behaviour)."""
    target = parse_database_url(value)
    if not target.is_loopback:
        raise ValueError(_MESSAGE)
    return target.url

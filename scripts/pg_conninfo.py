"""Turn a database URL from the environment into a password-free libpq conninfo (ADR-174).

Usage (from scripts/migrate_db_to_neon.sh):
    python3 scripts/pg_conninfo.py <ENV_VAR_NAME> <PGPASS_FILE>

Reads the URL from the named environment variable (never from argv), appends its password to
PGPASS_FILE (created with mode 0600), and prints a conninfo string WITHOUT the password. The
caller exports PGPASSFILE so pg_dump/pg_restore/psql read the password from that file. The
URL, the password and the file contents are never printed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

SCHEMES = {"postgresql", "postgres", "postgresql+psycopg"}
# Query keys passed through to libpq; everything else is refused (ADR-166 spirit).
ALLOWED_QUERY = {"sslmode", "channel_binding"}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")


class ConninfoError(ValueError):
    """Fixed codes only; never the URL or any part of it."""


def split_url(url: str) -> tuple[dict[str, str], str]:
    """Return (password-free conninfo parameters, password)."""
    parts = urlsplit(url)
    if parts.scheme not in SCHEMES or not parts.hostname or parts.fragment:
        raise ConninfoError("database_url_invalid")
    user = unquote(parts.username or "")
    password = unquote(parts.password or "")
    database = parts.path.lstrip("/")
    if not user or not password or not NAME.fullmatch(database):
        raise ConninfoError("database_url_incomplete")
    try:
        port = parts.port or 5432
    except ValueError:
        raise ConninfoError("database_url_invalid") from None
    params = {
        "host": parts.hostname,
        "port": str(port),
        "user": user,
        "dbname": database,
    }
    for key, value in parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False):
        if key not in ALLOWED_QUERY:
            raise ConninfoError("database_url_parameter_refused")
        params[key] = value
    return params, password


def conninfo(params: dict[str, str]) -> str:
    """libpq key='value' form; backslash and quote escaped."""

    def quote(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    return " ".join(f"{key}={quote(value)}" for key, value in params.items())


def pgpass_line(params: dict[str, str], password: str) -> str:
    """One ~/.pgpass line; ':' and '\\' escaped as libpq requires."""

    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:")

    fields = (params["host"], params["port"], params["dbname"], params["user"], password)
    if any("\n" in field or "\r" in field for field in fields):
        raise ConninfoError("database_url_invalid")
    return ":".join(escape(field) for field in fields) + "\n"


def append_pgpass(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)  # also when the file already existed
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", argv[0]):
        print("usage: pg_conninfo.py <ENV_VAR_NAME> <PGPASS_FILE>", file=sys.stderr)
        return 2
    url = os.environ.get(argv[0], "")
    try:
        params, password = split_url(url)
        append_pgpass(Path(argv[1]), pgpass_line(params, password))
    except ConninfoError as error:
        print(f"[conninfo] {error}", file=sys.stderr)
        return 1
    print(conninfo(params))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

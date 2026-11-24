"""Apply database migrations to the managed database from a Windows PC (runbook step 2).

Why this exists: `alembic upgrade` on Windows builds the API's own engine, which asks for a Linux CA
file that is not there, so the TLS check fails before anything runs. This script builds the engine
itself with the CA bundle shipped in the project's venv (certifi), then hands it to Alembic. The
migration files and env.py are the project's own.

Dry run by default: it prints the current revision and the target, and changes nothing.
With --execute it applies migrations up to --target, in order, each in its own transaction.

The database URL is read from VPN_DATABASE_URL (set it in the window with the secure prompt in
scripts/beta_backend_status.ps1, or with the same hidden prompt below). It is never printed.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import certifi
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="0029_window_hot_path_indexes")
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()

    url = os.environ.get("VPN_DATABASE_URL")
    if not url:
        # Ask for the parts one by one, so no '@' has to be typed or pasted. Password is hidden.
        user = input("Database user [neondb_owner]: ").strip() or "neondb_owner"
        password = getpass.getpass("Database password (hidden): ")
        host = input("Host, the part after '@' (for example ep-xxx.eu-central-1.aws.neon.tech): ").strip()
        dbname = input("Database name [neondb]: ").strip() or "neondb"
        url = (f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}/{dbname}"
               "?sslmode=verify-full")
    # Tolerate what Neon's snippet adds: surrounding quotes, the psycopg scheme, and its default
    # sslmode=require with channel_binding. The API needs verify-full, so that is what is used.
    url = url.strip().strip("'\"").replace("postgresql+psycopg://", "postgresql://", 1)
    url = url.replace("&channel_binding=require", "").replace("?channel_binding=require&", "?")
    url = url.replace("sslmode=require", "sslmode=verify-full")
    parts = urlsplit(url)
    # Never print the URL itself: it contains the password.
    if parts.scheme != "postgresql" or not parts.username or not parts.password or not parts.hostname:
        print("refused: the URL must look like postgresql://user:password@host/dbname?sslmode=verify-full")
        print(f"         received: length={len(url)}, scheme={parts.scheme!r}, "
              f"user_set={bool(parts.username)}, password_set={bool(parts.password)}, "
              f"host_set={bool(parts.hostname)}, count_of_@={url.count('@')}")
        print("         (nothing secret is printed; the password itself is not shown)")
        return 2
    if parse_qs(parts.query).get("sslmode") != ["verify-full"]:
        print("refused: the URL must end with sslmode=verify-full (the API refuses other modes).")
        return 2
    engine = create_engine(
        url.replace("postgresql://", "postgresql+psycopg://", 1),
        connect_args={"sslrootcert": certifi.where(), "connect_timeout": 10},
    )

    with engine.connect() as conn:
        try:
            current = conn.execute(text("select version_num from alembic_version")).scalars().all()
        except Exception:  # noqa: BLE001 - empty database has no version table
            current = []
    print(f"current revision: {current or 'none'}")
    print(f"target revision:  {a.target}")
    if not a.execute:
        print("mode=preflight_only; run again with --execute to apply")
        return 0

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_engine"] = engine
    command.upgrade(cfg, a.target)
    with engine.connect() as conn:
        after = conn.execute(text("select version_num from alembic_version")).scalars().all()
    print(f"applied. revision now: {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

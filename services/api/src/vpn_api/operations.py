"""Bounded local operations. No HTTP endpoints, external notifications or plaintext dumps."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Timer
from typing import Literal
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Connection, Engine, create_engine, inspect, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from vpn_api.db.models import User
from vpn_api.quota.service import LedgerService

MAX_DUMP = 32 * 1024 * 1024
MAX_TOKEN = 64 * 1024 * 1024
TIMEOUT = 60
ROOT = Path(__file__).resolve().parents[4]


class OperationsError(Exception):
    """Only stable codes may be shown to operators."""


class TableDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rows: int
    sha256: str


class Backup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    created_at: datetime
    source: str
    tables: dict[str, TableDigest]
    dump: str


def local_url(raw: str) -> URL:
    url = make_url(raw)
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg"}
        or url.host not in {"127.0.0.1", "::1"}
        or url.query
        # libpq expands dbname values containing '=' or a postgres URI. Restrict
        # names so PGDATABASE can never smuggle connection options past loopback.
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", url.database or "")
        or not url.username
    ):
        raise OperationsError("local_database_required")
    return url.set(drivername="postgresql+psycopg")


def local_engine(raw: str) -> Engine:
    return create_engine(
        local_url(raw), connect_args={"connect_timeout": 5, "options": "-c statement_timeout=60000"}
    )


def source_id(url: URL) -> str:
    # Password rotations must not invalidate an otherwise matching source.
    return hashlib.sha256(
        json.dumps([url.host, url.port or 5432, url.database, url.username]).encode()
    ).hexdigest()


def pg_environment(url: URL) -> dict[str, str]:
    # Discard inherited libpq overrides, service files and PGPASSFILE.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    env.update(
        PGHOST=url.host or "127.0.0.1",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
        PGDATABASE=url.database or "",
        PGCONNECT_TIMEOUT="5",
        PGOPTIONS="-c statement_timeout=60000",
        PGSSLMODE="disable",
    )
    return env


def tool(binary_dir: Path, name: str) -> str:
    path = binary_dir.resolve() / (name + (".exe" if os.name == "nt" else ""))
    if not path.is_file():
        raise OperationsError("postgres_tool_missing")
    return str(path)


def exclusive_write(path: Path, value: bytes) -> None:
    # No overwrite, including a symlink at the destination. Parent ACL is operator-owned.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise OperationsError("backup_size_limit")
    return value


def cipher(key: Path) -> Fernet:
    return Fernet(read_bounded(key, 44))


def load_backup(path: Path, key: Path) -> Backup:
    try:
        value = Backup.model_validate_json(cipher(key).decrypt(read_bounded(path, MAX_TOKEN)))
        if value.created_at.tzinfo is None or len(value.dump) > (MAX_DUMP + 2) // 3 * 4:
            raise OperationsError("backup_invalid")
        return value
    except (InvalidToken, ValidationError, ValueError) as exc:
        raise OperationsError("backup_invalid") from exc


def head_revision() -> str:
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    if head is None:
        raise OperationsError("schema_head_missing")
    return head


def check_schema(conn: Connection) -> None:
    if list(conn.scalars(text("SELECT version_num FROM alembic_version"))) != [head_revision()]:
        raise OperationsError("schema_mismatch")


def manifest(conn: Connection) -> dict[str, TableDigest]:
    result: dict[str, TableDigest] = {}
    for name in sorted(inspect(conn).get_table_names(schema="public")):
        quoted = conn.dialect.identifier_preparer.quote_identifier(name)
        digest = hashlib.sha256()
        count = 0
        # Sorted canonical JSONB representation, with unambiguous length framing.
        statement = text(
            f"SELECT to_jsonb(t)::text FROM public.{quoted} t "  # noqa: S608 - quoted identifier.
            'ORDER BY to_jsonb(t)::text COLLATE "C"'
        )
        for row in conn.execution_options(stream_results=True).scalars(statement):
            encoded = row.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            count += 1
        result[name] = TableDigest(rows=count, sha256=digest.hexdigest())
    return result


def make_backup(engine: Engine, binary_dir: Path, output: Path, key: Path) -> Backup:
    url = local_url(engine.url.render_as_string(hide_password=False))
    encryption = cipher(key)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            check_schema(conn)
            snapshot = conn.scalar(text("SELECT pg_export_snapshot()"))
            created = conn.scalar(text("SELECT clock_timestamp()"))
            command = [
                tool(binary_dir, "pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--no-password",
                "--snapshot=" + str(snapshot),
            ]
            with subprocess.Popen(  # noqa: S603 - fixed binary/options, secrets only in child env.
                command, env=pg_environment(url), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            ) as process:
                timer = Timer(TIMEOUT, process.kill)
                timer.start()
                try:
                    if process.stdout is None:
                        raise OperationsError("dump_failed")
                    dump = process.stdout.read(MAX_DUMP + 1)
                    if len(dump) > MAX_DUMP:
                        process.kill()
                        raise OperationsError("backup_size_limit")
                    if process.wait(timeout=TIMEOUT) or not dump.startswith(b"PGDMP"):
                        raise OperationsError("dump_failed")
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=TIMEOUT)
            value = Backup(
                created_at=created,
                source=source_id(url),
                tables=manifest(conn),
                dump=base64.b64encode(dump).decode("ascii"),
            )
    token = encryption.encrypt(value.model_dump_json().encode())
    if len(token) > MAX_TOKEN:
        raise OperationsError("backup_size_limit")
    exclusive_write(output, token)
    return value


def restore_drill(engine: Engine, binary_dir: Path, archive: Path, key: Path) -> dict[str, int]:
    # Authenticate and validate before creating any database.
    backup = load_backup(archive, key)
    try:
        dump = base64.b64decode(backup.dump, validate=True)
    except ValueError as exc:
        raise OperationsError("backup_invalid") from exc
    if not dump.startswith(b"PGDMP") or len(dump) > MAX_DUMP:
        raise OperationsError("backup_invalid")
    url = local_url(engine.url.render_as_string(hide_password=False))
    executable = tool(binary_dir, "pg_restore")
    name = "vpn_i016_drill_" + uuid4().hex
    target: Engine | None = None
    created = False
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
        try:
            # Generated identifier, never a supplied database name; template0 is empty.
            admin.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0')
            created = True
            target_url = url.set(database=name)
            target = local_engine(target_url.render_as_string(hide_password=False))
            result = subprocess.run(  # noqa: S603 - fixed trusted binary/options, bounded stdin.
                [
                    executable,
                    "--dbname=" + name,
                    "--no-owner",
                    "--no-privileges",
                    "--no-password",
                    "--exit-on-error",
                    "--single-transaction",
                ],
                input=dump,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=pg_environment(target_url),
                timeout=TIMEOUT,
                check=False,
            )
            if result.returncode:
                raise OperationsError("restore_failed")
            with target.connect() as conn:
                conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                check_schema(conn)
                if manifest(conn) != backup.tables:
                    raise OperationsError("restore_manifest_mismatch")
            accounts = 0
            with Session(target) as session, session.begin():
                for user_id in session.scalars(select(User.id).order_by(User.id)):
                    if not LedgerService(target).reconcile(session, user_id).consistent:
                        raise OperationsError("restore_ledger_inconsistent")
                    accounts += 1
            return {"tables": len(backup.tables), "accounts_reconciled": accounts}
        finally:
            if target is not None:
                target.dispose()
            if created:
                # Only our exact random database can reach this statement.
                admin.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')


def monitor_database(engine: Engine, now: datetime) -> dict[str, int]:
    local_url(engine.url.render_as_string(hide_password=False))
    counts: dict[str, int] = {}
    with engine.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = 5000"))
        check_schema(conn)
        nodes = conn.execute(text("SELECT last_seen_at FROM vpn_nodes WHERE status='active'"))
        seen = list(nodes.scalars())
        if not seen:
            counts["node_inventory_empty"] = 1
        stale = sum(
            value is None or value > now or now - value >= timedelta(seconds=90) for value in seen
        )
        if stale:
            counts["node_heartbeat_stale"] = stale
        pending = conn.scalar(
            text(
                "SELECT count(*) FROM peer_commands c JOIN tunnels t ON t.id=c.tunnel_id "
                "WHERE c.acknowledged_at IS NULL AND c.created_at <= :cutoff "
                "AND c.peer_version=t.peer_version AND t.state <> 'revoked'"
            ),
            {"cutoff": now - timedelta(seconds=90)},
        )
        if pending:
            counts["peer_command_stalled"] = int(pending)
    return counts


def monitor(
    engine: Engine,
    archive: Path,
    key: Path,
    disk: Path,
    *,
    now: datetime | None = None,
    min_free_bytes: int = 1024**3,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None or min_free_bytes < 0:
        raise OperationsError("monitor_config_invalid")
    alerts: dict[str, int] = {}
    try:
        alerts.update(monitor_database(engine, now))
    except (SQLAlchemyError, OperationsError):
        alerts["database_check_failed"] = 1
    try:
        backup = load_backup(archive, key)
        if backup.source != source_id(engine.url):
            alerts["backup_source_mismatch"] = 1
        age = now - backup.created_at
        if age < timedelta(0) or age > timedelta(hours=24):
            alerts["backup_stale"] = 1
    except (OSError, ValueError, OperationsError):
        alerts["backup_unavailable"] = 1
    try:
        if shutil.disk_usage(disk).free < min_free_bytes:
            alerts["disk_space_low"] = 1
    except OSError:
        alerts["disk_check_failed"] = 1
    return alerts

"""Beta backup guard tests; synthetic engine and ciphertext writer only."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.engine import Engine
from test_migrations import migration_config

from vpn_api import beta_backup, operations
from vpn_api.db.connection import transaction
from vpn_api.db.models import User
from vpn_api.operations import OperationsError

ROOT = Path(__file__).resolve().parents[3]


def test_backup_refuses_unmounted_target_before_database_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def dump(*args: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(beta_backup, "make_backup", dump)
    engine = cast(Engine, object())
    with pytest.raises(OperationsError, match="backup_target_not_mounted"):
        beta_backup.create_beta_backup(
            engine, Path("/usr/bin"), tmp_path, Path("/key"), mount_check=lambda _: False
        )
    assert not called
    assert list(tmp_path.iterdir()) == []


def test_backup_uses_unique_timestamped_encrypted_archive_on_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs: list[Path] = []

    def dump(engine: Any, postgres_bin: Path, archive: Path, key_file: Path) -> None:
        assert postgres_bin == Path("/usr/bin")
        assert key_file == Path("/key")
        archive.write_bytes(b"ENCRYPTED TEST ONLY")
        outputs.append(archive)

    monkeypatch.setattr(beta_backup, "make_backup", dump)
    engine = cast(Engine, object())
    timestamp = datetime(2026, 10, 25, 12, 34, 56, tzinfo=UTC)
    for _ in range(2):
        beta_backup.create_beta_backup(
            engine,
            Path("/usr/bin"),
            tmp_path,
            Path("/key"),
            now=timestamp,
            mount_check=lambda _: True,
        )

    assert len(outputs) == 2 and outputs[0] != outputs[1]
    assert all(path.parent == tmp_path.resolve() and path.suffix == ".enc" for path in outputs)
    assert all(path.read_bytes() == b"ENCRYPTED TEST ONLY" for path in outputs)


def test_latest_backup_replaces_only_after_success_and_keeps_one_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def dump(engine: Any, postgres_bin: Path, archive: Path, key_file: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationsError("dump_failed")
        archive.write_bytes(f"ENCRYPTED TEST ONLY {calls}".encode())

    monkeypatch.setattr(beta_backup, "make_backup", dump)
    engine = cast(Engine, object())
    directory_owner = lambda _: True  # noqa: E731 - explicit TEST ONLY owner-check substitute.
    latest = beta_backup.create_latest_beta_backup(
        engine, Path("/usr/bin"), tmp_path, Path("/key"), target_check=directory_owner
    )
    first = latest.read_bytes()
    assert first == b"ENCRYPTED TEST ONLY 1"

    with pytest.raises(OperationsError, match="dump_failed"):
        beta_backup.create_latest_beta_backup(
            engine, Path("/usr/bin"), tmp_path, Path("/key"), target_check=directory_owner
        )

    assert latest.read_bytes() == first
    assert [item.name for item in tmp_path.iterdir()] == ["latest.enc"]


def test_latest_backup_rejects_wrong_directory_owner_before_database_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def dump(*args: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(beta_backup, "make_backup", dump)
    with pytest.raises(OperationsError, match="backup_target_owner_invalid"):
        beta_backup.create_latest_beta_backup(
            cast(Engine, object()),
            Path("/usr/bin"),
            tmp_path,
            Path("/key"),
            target_check=lambda _: False,
        )
    assert not called
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def postgres_binaries() -> Path:
    path = Path(os.environ.get("VPN_TEST_POSTGRES_BIN", ROOT / ".cache/i-004/portable/pgsql/bin"))
    assert Path(operations.tool(path, "pg_dump")).is_file()
    assert Path(operations.tool(path, "pg_restore")).is_file()
    return path


@pytest.mark.postgres
def test_beta_backup_wrapper_writes_real_encrypted_archive_and_restores(
    engine: Engine, postgres_binaries: Path, tmp_path: Path
) -> None:
    command.upgrade(migration_config(engine), "head")
    with transaction(engine) as session:
        session.add(User(status="active"))

    key = tmp_path / "backup.key"
    operations.exclusive_write(key, Fernet.generate_key())
    archive = beta_backup.create_beta_backup(
        engine,
        postgres_binaries,
        tmp_path,
        key,
        mount_check=lambda _: True,  # TEST ONLY: this temporary directory is not off-host storage.
    )

    assert archive.exists() and archive.suffix == ".enc"
    assert not archive.read_bytes().startswith(b"PGDMP")
    assert operations.load_backup(archive, key).source == operations.source_id(engine.url)
    assert operations.restore_drill(engine, postgres_binaries, archive, key) == {
        "tables": len(operations.load_backup(archive, key).tables),
        "accounts_reconciled": 1,
    }
    with engine.connect() as connection:
        leftovers = list(
            connection.scalars(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'vpn_i016_drill_%'")
            )
        )
    assert leftovers == []
    assert {item.name for item in tmp_path.iterdir()} == {key.name, archive.name}

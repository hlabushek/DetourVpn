"""Local encrypted archive import tests; no network or real user data."""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from cryptography.fernet import Fernet
from scripts.import_beta_backup import import_backup
from sqlalchemy.engine import Engine
from test_migrations import migration_config

from vpn_api import beta_backup, operations
from vpn_api.db.connection import transaction
from vpn_api.db.models import User
from vpn_api.operations import Backup, OperationsError, TableDigest

ROOT = Path(__file__).resolve().parents[3]


def _archive(path: Path, key_path: Path) -> None:
    key = Fernet.generate_key()
    operations.exclusive_write(key_path, key)
    value = Backup(
        created_at=datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
        source="test-only-source-fingerprint",
        tables={"users": TableDigest(rows=1, sha256="0" * 64)},
        dump="UEdETVA=",
    )
    operations.exclusive_write(path, Fernet(key).encrypt(value.model_dump_json().encode()))


def test_import_validates_and_copies_ciphertext_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "download.enc"
    key = tmp_path / "local.key"
    destination = tmp_path / "local-backups"
    destination.mkdir()
    _archive(source, key)
    original = source.read_bytes()

    imported = import_backup(source, destination, key)

    assert imported.parent == destination
    assert imported.suffix == ".enc"
    assert imported.read_bytes() == original
    assert source.read_bytes() == original
    assert operations.load_backup(imported, key).source == "test-only-source-fingerprint"
    assert len(list(destination.iterdir())) == 1


def test_import_rejects_invalid_ciphertext_without_creating_copy(tmp_path: Path) -> None:
    source = tmp_path / "download.enc"
    key = tmp_path / "local.key"
    destination = tmp_path / "local-backups"
    destination.mkdir()
    operations.exclusive_write(key, Fernet.generate_key())
    operations.exclusive_write(source, b"not encrypted")

    with pytest.raises(OperationsError, match="backup_invalid"):
        import_backup(source, destination, key)

    assert list(destination.iterdir()) == []


def test_import_refuses_symlinked_destination(tmp_path: Path) -> None:
    source = tmp_path / "download.enc"
    key = tmp_path / "local.key"
    real_destination = tmp_path / "real-backups"
    real_destination.mkdir()
    _archive(source, key)
    link = tmp_path / "linked-backups"
    try:
        link.symlink_to(real_destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(OperationsError, match="backup_destination_invalid"):
        import_backup(source, link, key)

    assert list(real_destination.iterdir()) == []


@pytest.mark.postgres
def test_imported_real_backup_restores_in_disposable_database(
    engine: Engine, tmp_path: Path
) -> None:
    """TEST ONLY: exercise import plus restore against the disposable PostgreSQL fixture."""
    postgres_bin = Path(
        os.environ.get("VPN_TEST_POSTGRES_BIN", ROOT / ".cache/i-004/portable/pgsql/bin")
    )
    command.upgrade(migration_config(engine), "head")
    with transaction(engine) as session:
        session.add(User(status="active"))

    key = tmp_path / "backup.key"
    operations.exclusive_write(key, Fernet.generate_key())
    server_archive_dir = tmp_path / "server-output"
    server_archive_dir.mkdir()
    server_archive = beta_backup.create_beta_backup(
        engine,
        postgres_bin,
        server_archive_dir,
        key,
        mount_check=lambda _: True,  # TEST ONLY: a temporary directory is not a mount.
    )
    downloaded = tmp_path / "downloaded.enc"
    shutil.copyfile(server_archive, downloaded)
    owner_archive_dir = tmp_path / "owner-backups"
    owner_archive_dir.mkdir()
    imported = import_backup(downloaded, owner_archive_dir, key)

    result = operations.restore_drill(engine, postgres_bin, imported, key)

    assert result["accounts_reconciled"] == 1
    assert len(list(owner_archive_dir.glob("*.enc"))) == 1


@pytest.mark.postgres
def test_owner_beta_archive_restores_in_disposable_database(engine: Engine) -> None:
    """TEST ONLY: restore a caller-selected encrypted beta archive; never inspect row data."""
    archive_value = os.environ.get("I027_VERIFY_ARCHIVE")
    key_value = os.environ.get("I027_VERIFY_KEY_FILE")
    if not archive_value or not key_value:
        pytest.skip("I-027 owner archive verification inputs are absent")
    archive = Path(archive_value)
    key = Path(key_value)
    if archive.is_symlink() or key.is_symlink() or not archive.is_file() or not key.is_file():
        pytest.fail("I-027 archive verification inputs are invalid")

    operations.load_backup(archive, key)
    postgres_bin = Path(
        os.environ.get("VPN_TEST_POSTGRES_BIN", ROOT / ".cache/i-004/portable/pgsql/bin")
    )
    operations.restore_drill(engine, postgres_bin, archive, key)

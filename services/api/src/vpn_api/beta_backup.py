"""Create encrypted beta backups on a mounted target or a fixed local staging slot."""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import Engine

from vpn_api.operations import OperationsError, make_backup


def create_beta_backup(
    engine: Engine,
    postgres_bin: Path,
    archive_dir: Path,
    key_file: Path,
    *,
    now: datetime | None = None,
    mount_check: Callable[[Path], bool] | None = None,
) -> Path:
    """Write an encrypted archive to a direct mount point; never fall back locally.

    `mount_check` exists for deterministic unit tests. Production uses Path.is_mount.
    """
    if archive_dir.is_symlink():
        raise OperationsError("backup_target_invalid")
    try:
        target = archive_dir.resolve(strict=True)
    except OSError as exc:
        raise OperationsError("backup_target_invalid") from exc
    if target != archive_dir.absolute() or not target.is_dir():
        raise OperationsError("backup_target_invalid")
    mounted = (mount_check or Path.is_mount)(target)
    if not mounted:
        raise OperationsError("backup_target_not_mounted")

    created = now or datetime.now(UTC)
    if created.tzinfo is None:
        raise OperationsError("backup_time_invalid")
    stamp = created.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = target / f"detourvpn-beta-{stamp}-{uuid4().hex}.enc"
    make_backup(engine, postgres_bin, archive, key_file)
    return archive


def create_latest_beta_backup(
    engine: Engine,
    postgres_bin: Path,
    archive_dir: Path,
    key_file: Path,
    *,
    target_check: Callable[[Path], bool] | None = None,
) -> Path:
    """Atomically replace the single encrypted beta staging archive.

    This is intended for the owner-PC pull workflow, not general backup storage.
    The target must be a private, pre-created directory owned by the service user;
    deployment configures that boundary. A failed dump leaves the prior archive intact.
    """
    if archive_dir.is_symlink():
        raise OperationsError("backup_target_invalid")
    try:
        target = archive_dir.resolve(strict=True)
    except OSError as exc:
        raise OperationsError("backup_target_invalid") from exc
    if target != archive_dir.absolute() or not target.is_dir():
        raise OperationsError("backup_target_invalid")
    if target_check is not None:
        owned = target_check(target)
    else:
        owned = hasattr(os, "geteuid") and target.stat().st_uid == os.geteuid()
    if not owned:
        raise OperationsError("backup_target_owner_invalid")

    latest = target / "latest.enc"
    if latest.is_symlink() or (latest.exists() and not latest.is_file()):
        raise OperationsError("backup_target_invalid")
    staged = target / f".latest-{uuid4().hex}.enc"
    try:
        make_backup(engine, postgres_bin, staged, key_file)
        os.replace(staged, latest)
        if os.name == "posix":
            directory_fd = os.open(target, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return latest

"""Verify and safely import one already-downloaded encrypted beta backup."""

import argparse
import json
import sys
from datetime import UTC
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.operations import (  # noqa: E402
    MAX_TOKEN,
    OperationsError,
    exclusive_write,
    load_backup,
    read_bounded,
)


def import_backup(source: Path, destination_dir: Path, key_file: Path) -> Path:
    """Validate ciphertext and write an exclusive, atomic local copy.

    The source file is never moved or removed. Destination directory and key are
    operator-managed local paths; no network transfer or secret output occurs here.
    """
    if source.is_symlink() or not source.is_file() or source.suffix != ".enc":
        raise OperationsError("backup_source_invalid")
    if key_file.is_symlink() or not key_file.is_file():
        raise OperationsError("backup_key_invalid")
    if destination_dir.is_symlink() or not destination_dir.is_dir():
        raise OperationsError("backup_destination_invalid")

    payload = read_bounded(source, MAX_TOKEN)
    backup = load_backup(source, key_file)
    stamp = backup.created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    name = f"detourvpn-beta-{stamp}-{uuid4().hex}.enc"
    target = destination_dir / name
    staged = destination_dir / f".{name}.{uuid4().hex}.part"
    try:
        exclusive_write(staged, payload)
        # Hard-link creation is atomic and fails if the destination already exists.
        target.hardlink_to(staged)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    staged.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Downloaded .enc archive")
    parser.add_argument("--destination-dir", type=Path, required=True, help="Existing local folder")
    parser.add_argument("--key-file", type=Path, required=True, help="Local Fernet key file")
    args = parser.parse_args()
    try:
        archive = import_backup(args.source, args.destination_dir, args.key_file)
    except (OperationsError, OSError, ValueError):
        print('{"code":"backup_import_failed"}')
        return 2
    print(json.dumps({"code": "backup_imported", "archive": archive.name}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

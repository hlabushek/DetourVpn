"""Fail-closed readiness check for the separately provisioned beta node slots."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Any

CONFIG = Path("/etc/detourvpn-node/agent.json")
SLOTS = Path("/etc/detourvpn-node/slots.json")
READY = Path("/run/detourvpn-node/slots")


class PreflightError(Exception):
    """Fixed diagnostic only; do not include key material or paths."""


def _private_json(path: Path, limit: int) -> dict[str, Any]:
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise PreflightError("private_node_file_untrusted")
        for parent in path.parents:
            parent_info = parent.lstat()
            if (
                not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != 0
                or parent_info.st_mode & 0o022
            ):
                raise PreflightError("private_node_directory_untrusted")
        with os.fdopen(fd, "rb", closefd=True) as source:
            fd = -1
            raw = source.read(limit + 1)
        if len(raw) != info.st_size:
            raise PreflightError("private_node_file_changed")
        value = json.loads(raw)
    except PreflightError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise PreflightError("private_node_file_invalid") from None
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(value, dict):
        raise PreflightError("private_node_file_invalid")
    return value


def _key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return len(raw) == 32 and raw != bytes(32) and base64.b64encode(raw).decode() == value


def check_readiness(
    config_path: Path = CONFIG,
    slots_path: Path = SLOTS,
    ready_dir: Path = READY,
) -> int:
    """Require every configured persistent slot to have its matching runtime marker."""
    config = _private_json(config_path, 4096)
    slots_config = _private_json(slots_path, 65536)
    count = config.get("slot_count")
    slots = slots_config.get("slots")
    if (
        type(count) is not int
        or not 1 <= count <= 16
        or slots_config.get("version") != 1
        or not isinstance(slots, list)
        or len(slots) != count
    ):
        raise PreflightError("slot_configuration_invalid")
    try:
        ready_dir_info = ready_dir.lstat()
        if (
            not stat.S_ISDIR(ready_dir_info.st_mode)
            or ready_dir_info.st_uid != 0
            or ready_dir_info.st_mode & 0o077
        ):
            raise PreflightError("readiness_directory_untrusted")
    except OSError:
        raise PreflightError("slot_readiness_missing") from None

    for slot_id, slot in enumerate(slots, 1):
        if (
            not isinstance(slot, dict)
            or type(slot.get("slot_id")) is not int
            or slot["slot_id"] != slot_id
            or not _key(slot.get("server_public_key"))
            or not _key(slot.get("server_private_key"))
            or not _key(slot.get("header_protection_key"))
        ):
            raise PreflightError("slot_configuration_invalid")
        ready = _private_json(ready_dir / f"{slot_id}.json", 4096)
        if (
            type(ready.get("slot_id")) is not int
            or ready.get("slot_id") != slot_id
            or type(ready.get("native_pid")) is not int
            or ready.get("native_pid", 0) <= 1
            or type(ready.get("native_netns_inode")) is not int
            or ready.get("native_netns_inode", 0) <= 0
            or ready.get("server_public_key") != slot["server_public_key"]
        ):
            raise PreflightError("slot_readiness_conflict")
    return count


def main() -> int:
    try:
        count = check_readiness()
    except PreflightError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps({"ready_slots": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

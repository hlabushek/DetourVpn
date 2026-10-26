"""Render persistent beta node and API config from a private source file."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import cast

NODE_SOURCE = Path(__file__).resolve().parents[1] / "services" / "node-agent"
sys.path.insert(0, str(NODE_SOURCE))

from beta_slot_config import ConfigError, render_configs  # noqa: E402


class OutputError(Exception):
    """Fixed output diagnostics without filesystem paths or secret values."""


def _effective_uid() -> int:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        raise OutputError("private_posix_output_required")
    return cast(int, getter())


def _outside_repository(path: Path, repository: Path) -> Path:
    if path.is_symlink() or path.parent.is_symlink():
        raise OutputError("path_symlink_forbidden")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(repository)
    except ValueError:
        return resolved
    raise OutputError("secret_file_must_be_outside_repository")


def _read_source(path: Path, repository: Path) -> bytes:
    try:
        if path.is_symlink():
            raise OutputError("source_file_not_private_regular_file")
        resolved = _outside_repository(path, repository)
        info = resolved.lstat()
        if not stat.S_ISREG(info.st_mode) or resolved.is_symlink() or info.st_nlink != 1:
            raise OutputError("source_file_not_private_regular_file")
        if os.name != "nt" and (info.st_uid != _effective_uid() or info.st_mode & 0o077):
            raise OutputError("source_file_permissions_invalid")
        parent_info = resolved.parent.lstat()
        if os.name != "nt" and (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != _effective_uid()
            or parent_info.st_mode & 0o022
        ):
            raise OutputError("source_directory_permissions_invalid")
        if info.st_size > 65536:
            raise OutputError("source_file_oversized")
        raw = resolved.read_bytes()
    except OSError:
        raise OutputError("source_file_unavailable") from None
    if len(raw) != info.st_size:
        raise OutputError("source_file_changed_during_read")
    return raw


def _write_file(directory: Path, name: str, content: bytes) -> None:
    path = directory / name
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)
    except OSError:
        raise OutputError("secure_output_write_failed") from None


def write_configs(
    source_path: Path,
    output_path: Path,
    repository: Path,
) -> list[str]:
    """Render outputs into a new 0700 directory outside the checkout."""
    if os.name != "posix":
        raise OutputError("private_posix_output_required")
    repo = repository.resolve(strict=True)
    output_parent = _outside_repository(output_path.parent, repo)
    if os.name != "nt":
        parent_info = output_parent.lstat()
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != _effective_uid()
            or parent_info.st_mode & 0o077
        ):
            raise OutputError("output_parent_permissions_invalid")
    try:
        output_path.resolve(strict=False).relative_to(repo)
    except ValueError:
        pass
    else:
        raise OutputError("secret_output_must_be_outside_repository")
    raw = _read_source(source_path, repo)
    rendered = render_configs(raw)
    if output_path.exists() or output_path.is_symlink():
        raise OutputError("output_path_already_exists")
    try:
        output_path.mkdir(mode=0o700, parents=False, exist_ok=False)
        node_dir = output_path / "node"
        api_dir = output_path / "api"
        node_dir.mkdir(mode=0o700)
        awg_dir = node_dir / "awg"
        awg_dir.mkdir(mode=0o700)
        api_dir.mkdir(mode=0o700)
        _write_file(node_dir, "agent.json", rendered["agent_config"])
        _write_file(node_dir, "slots.json", rendered["node_slots"])
        for name, config in rendered["awg_configs"].items():
            _write_file(awg_dir, name, config)
        _write_file(api_dir, "window-manifest.json", rendered["api_manifest"])
    except FileExistsError:
        raise OutputError("output_path_already_exists") from None
    except OSError:
        raise OutputError("secure_output_write_failed") from None
    return [
        "node/agent.json",
        "node/slots.json",
        *[f"node/awg/{name}" for name in rendered["awg_configs"]],
        "api/window-manifest.json",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        paths = write_configs(
            arguments.source,
            arguments.output_dir,
            Path(__file__).resolve().parents[1],
        )
    except (OutputError, ConfigError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"created": True, "files": paths}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

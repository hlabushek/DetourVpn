"""Build an allowlisted, unprovisioned v3 beta-node source bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TypedDict

EXPECTED_BINARIES = {
    "awg": "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce",
    "amneziawg-go": "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe",
}

SOURCE_FILES = (
    ("services/node-agent/window_daemon.py", "window_daemon.py"),
    ("services/node-agent/beta_node_preflight.py", "beta_node_preflight.py"),
    ("services/node-agent/beta_slot_bootstrap.py", "beta_slot_bootstrap.py"),
    ("services/node-agent/window_gate.py", "window_gate.py"),
    ("services/node-agent/window_peer.py", "window_peer.py"),
    ("services/node-agent/window-requirements.txt", "requirements.txt"),
    ("services/api/src/vpn_api/__init__.py", "vpn_api/__init__.py"),
    ("services/api/src/vpn_api/tunnels/__init__.py", "vpn_api/tunnels/__init__.py"),
    ("services/api/src/vpn_api/tunnels/keys.py", "vpn_api/tunnels/keys.py"),
    ("services/api/src/vpn_api/tunnels/awg_profile.py", "vpn_api/tunnels/awg_profile.py"),
    ("services/api/src/vpn_api/tunnels/v3_contracts.py", "vpn_api/tunnels/v3_contracts.py"),
    (
        "services/api/src/vpn_api/tunnels/window_journal.py",
        "vpn_api/tunnels/window_journal.py",
    ),
    (
        "services/api/src/vpn_api/tunnels/window_runtime.py",
        "vpn_api/tunnels/window_runtime.py",
    ),
    (
        "infra/beta-node/detourvpn-beta-node-agent.service",
        "systemd/detourvpn-beta-node-agent.service",
    ),
    (
        "infra/beta-node/detourvpn-beta-slot-bootstrap.service",
        "systemd/detourvpn-beta-slot-bootstrap.service",
    ),
    (
        "infra/beta-node/detourvpn-beta-core@.service",
        "systemd/detourvpn-beta-core@.service",
    ),
)


class PackageError(Exception):
    """Fixed package diagnostics only; never include paths or input contents."""


class PackageFile(TypedDict):
    path: str
    size: int
    sha256: str


class PackageManifest(TypedDict):
    schema_version: int
    package: str
    deployable: bool
    files: list[PackageFile]
    excludes: list[str]


def _regular_bytes(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError:
        raise PackageError("package_input_missing") from None
    if path.is_symlink() or not path.is_file():
        raise PackageError("package_input_not_regular")
    try:
        data = path.read_bytes()
    except OSError:
        raise PackageError("package_input_unreadable") from None
    if len(data) != info.st_size:
        raise PackageError("package_input_changed_during_read")
    return data


def _write_private(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, mode)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(mode)
    except OSError:
        raise PackageError("package_output_failed") from None


def build_package(repository: Path, artifact_directory: Path, output: Path) -> PackageManifest:
    """Create a new private package directory; never overwrite an existing path."""
    if repository.is_symlink() or not repository.is_dir():
        raise PackageError("repository_root_invalid")
    if artifact_directory.is_symlink() or not artifact_directory.is_dir():
        raise PackageError("artifact_directory_invalid")
    if output.exists() or output.is_symlink():
        raise PackageError("package_output_already_exists")
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise PackageError("package_output_parent_invalid")

    payload: dict[str, tuple[bytes, int]] = {}
    for relative, destination in SOURCE_FILES:
        payload[destination] = (_regular_bytes(repository / relative), 0o600)
    for name, expected_hash in EXPECTED_BINARIES.items():
        data = _regular_bytes(artifact_directory / name)
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise PackageError("pinned_node_artifact_mismatch")
        payload[f"bin/{name}"] = (data, 0o700)

    file_entries: list[PackageFile] = []
    for name, (data, _mode) in sorted(payload.items()):
        file_entries.append(
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest: PackageManifest = {
        "schema_version": 1,
        "package": "detourvpn-beta-node-v3",
        "deployable": False,
        "files": file_entries,
        "excludes": ["node credential", "agent configuration", "slot keys", "API secrets"],
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    checksum_bytes = "".join(
        f"{entry['sha256']}  {entry['path']}\n" for entry in file_entries
    ).encode()

    try:
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, (data, mode) in payload.items():
            destination_path = output / name
            destination_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_private(destination_path, data, mode)
        _write_private(output / "manifest.json", manifest_bytes, 0o600)
        _write_private(output / "SHA256SUMS", checksum_bytes, 0o600)
    except FileExistsError:
        raise PackageError("package_output_already_exists") from None
    except OSError:
        raise PackageError("package_output_failed") from None
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(".cache/i-010/artifacts"),
    )
    arguments = parser.parse_args(argv)
    try:
        repository = Path(__file__).resolve().parents[1]
        manifest = build_package(
            repository,
            arguments.artifact_dir,
            arguments.output_dir,
        )
    except PackageError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"created": True, "file_count": len(manifest["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

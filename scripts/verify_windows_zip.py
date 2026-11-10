"""Create a sanitized, local verification receipt for the unsigned Windows beta zip (I-032).

This checks the archive layout, the pinned component hashes, the executables' PE headers and the
embedded version and elevation manifest. It does not run anything, prove a tunnel handshake, egress
or routing, and does not accept the build for distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT_DIR = "DetourVpn"
APP_EXE = f"{ROOT_DIR}/DetourVpn.exe"
ENGINE_EXE = f"{ROOT_DIR}/detour-engine.exe"
WINTUN_DLL = f"{ROOT_DIR}/wintun.dll"
REQUIRED_FILES = (
    APP_EXE,
    ENGINE_EXE,
    WINTUN_DLL,
    f"{ROOT_DIR}/flutter_windows.dll",
    f"{ROOT_DIR}/data/app.so",
    f"{ROOT_DIR}/data/icudtl.dat",
    f"{ROOT_DIR}/README.txt",
    f"{ROOT_DIR}/THIRD_PARTY_NOTICES.md",
    f"{ROOT_DIR}/SOURCE/source-lock.json",
)
FORBIDDEN_SUFFIXES = {
    ".pdb", ".ilk", ".obj", ".exp", ".lib", ".key", ".pem", ".p12", ".pfx", ".jks", ".keystore",
    ".env", ".log", ".iml",
}  # fmt: skip
FORBIDDEN_NAMES = {"local.properties", "key.properties", ".env"}
MAX_ENTRIES = 3000
MAX_UNCOMPRESSED = 600 * 1024 * 1024
IMAGE_FILE_MACHINE_AMD64 = 0x8664


class VerificationError(Exception):
    """The archive failed verification; messages never contain paths outside the archive."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pe(data: bytes) -> dict[str, object]:
    """Minimal PE64 header reader: machine, signature directory and ProductVersion string."""
    if len(data) < 0x100 or data[:2] != b"MZ":
        raise VerificationError("Not a Windows executable")
    (pe_offset,) = struct.unpack_from("<I", data, 0x3C)
    if pe_offset + 0x108 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise VerificationError("Invalid PE header")
    (machine,) = struct.unpack_from("<H", data, pe_offset + 4)
    optional = pe_offset + 24
    (magic,) = struct.unpack_from("<H", data, optional)
    if magic != 0x20B:
        raise VerificationError("Only PE32+ (64-bit) executables are expected")
    # Data directories start at optional + 112; entry 4 is the Authenticode certificate table.
    _, security_size = struct.unpack_from("<II", data, optional + 112 + 4 * 8)
    product_version = None
    key = "ProductVersion".encode("utf-16le") + b"\0\0"
    at = data.find(key)
    if at >= 0:
        position = at + len(key)
        while data[position : position + 2] == b"\0\0":
            position += 2
        end = position
        while end + 1 < len(data) and data[end : end + 2] != b"\0\0":
            end += 2
        product_version = data[position:end].decode("utf-16le", errors="replace")
    return {
        "machine": machine,
        "signed": security_size != 0,
        "product_version": product_version,
        "requires_administrator": b"requireAdministrator" in data,
        "as_invoker": b"asInvoker" in data,
    }


def _check_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        raise VerificationError("Archive entry count is outside the expected range")
    entries: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        if (
            "\\" in name
            or name.startswith("/")
            or ".." in path.parts
            or re.match(r"^[A-Za-z]:", name)
            or path.parts[0] != ROOT_DIR
        ):
            raise VerificationError("Archive contains an unsafe or misplaced path")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise VerificationError("Archive contains a symbolic link")
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_UNCOMPRESSED:
            raise VerificationError("Archive expands beyond the expected size")
        lower = path.name.lower()
        if (
            path.suffix.lower() in FORBIDDEN_SUFFIXES
            or lower in FORBIDDEN_NAMES
            or lower.startswith(".env")
        ):
            raise VerificationError("Archive contains a file that must not be shipped")
        if name in entries:
            raise VerificationError("Archive contains duplicate entries")
        entries[name] = info
    return entries


def verify_zip(
    zip_path: Path, repo_root: Path, expected_version: str | None, output: Path
) -> dict[str, object]:
    zip_path = zip_path.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if output.exists():
        raise VerificationError("Receipt output already exists")
    lock = json.loads(
        (repo_root / "third_party" / "amnezia-box" / "source-lock.json").read_text("utf-8")
    )
    match = re.fullmatch(r"DetourVpn-windows-x64-(.+)\.zip", zip_path.name)
    if match is None:
        raise VerificationError("Archive name does not match DetourVpn-windows-x64-<version>.zip")
    version_name = match.group(1)
    if expected_version is not None and version_name != expected_version:
        raise VerificationError("Archive version does not match the expected version")

    artifact_sha256 = sha256_file(zip_path)
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise VerificationError("Archive is not a valid zip") from exc
    with archive:
        if archive.testzip() is not None:
            raise VerificationError("Archive failed its integrity check")
        entries = _check_entries(archive)
        for required in REQUIRED_FILES:
            if required not in entries:
                raise VerificationError(f"Archive is missing a required file: {required}")
        # GPL corresponding source: the exact archive and every local patch must ship and match.
        source_files = [lock["archive"], "source-lock.json", *lock["windows_patches"]]
        for name in source_files:
            entry = f"{ROOT_DIR}/SOURCE/{name}"
            if entry not in entries:
                raise VerificationError(f"Archive is missing the corresponding source file: {name}")
        source_hashes: dict[str, str] = {}
        for name in source_files:
            data = archive.read(f"{ROOT_DIR}/SOURCE/{name}")
            repo_copy = repo_root / "third_party" / "amnezia-box" / name
            if sha256_bytes(data) != sha256_file(repo_copy):
                raise VerificationError(f"Shipped source file differs from the repository: {name}")
            source_hashes[name] = sha256_bytes(data)
        if source_hashes[lock["archive"]] != lock["archive_sha256"]:
            raise VerificationError("Shipped source archive does not match the pinned SHA-256")

        wintun = archive.read(WINTUN_DLL)
        if sha256_bytes(wintun) != lock["windows_wintun"]["dll_sha256"]:
            raise VerificationError("wintun.dll does not match the pinned SHA-256")
        wintun_pe = read_pe(wintun)
        if wintun_pe["machine"] != IMAGE_FILE_MACHINE_AMD64:
            raise VerificationError("wintun.dll is not an x64 binary")

        engine = archive.read(ENGINE_EXE)
        engine_pe = read_pe(engine)
        if engine_pe["machine"] != IMAGE_FILE_MACHINE_AMD64 or len(engine) < 5 * 1024 * 1024:
            raise VerificationError("detour-engine.exe is not a plausible x64 engine build")

        app = archive.read(APP_EXE)
        app_pe = read_pe(app)
        if app_pe["machine"] != IMAGE_FILE_MACHINE_AMD64:
            raise VerificationError("DetourVpn.exe is not an x64 binary")
        if app_pe["signed"]:
            raise VerificationError("DetourVpn.exe carries an unexpected Authenticode signature")
        product_version = str(app_pe["product_version"] or "")
        if not product_version.startswith(version_name):
            raise VerificationError("DetourVpn.exe version does not match the archive version")
        if not app_pe["requires_administrator"] or app_pe["as_invoker"]:
            raise VerificationError("DetourVpn.exe manifest does not require administrator rights")

    receipt: dict[str, object] = {
        "observed_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "scope": "local unsigned Windows zip layout, pinned component hashes and PE metadata",
        "platform": "windows-x64",
        "application": "DetourVpn.exe",
        "version_name": version_name,
        "artifact_sha256": artifact_sha256,
        "components": {
            "DetourVpn.exe": sha256_bytes(app),
            "detour-engine.exe": sha256_bytes(engine),
            "wintun.dll": sha256_bytes(wintun),
        },
        "engine_upstream": lock["version"],
        "source_files_shipped": sorted(source_hashes),
        "signature": "unsigned",
        "elevation": "requireAdministrator",
        "verification": {
            "archive_layout": "passed",
            "pinned_component_hashes": "passed",
            "pe_metadata": "passed",
            "corresponding_source": "passed",
        },
        "runtime_verified": False,
        "tunnel_handshake": "not_verified",
        "distribution": "not_accepted",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise VerificationError("Sanitized receipt could not be written") from exc
    return receipt


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        verify_zip(args.zip, args.repo_root, args.expected_version, args.output)
    except (OSError, VerificationError, KeyError, ValueError) as exc:
        print(f"Windows zip verification failed: {exc}", file=sys.stderr)
        return 1
    print("Windows zip verified; sanitized receipt created. Runtime remains unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Create a sanitized, local verification receipt for a signed beta APK.

This verifies APK metadata and its signature with Android SDK tools. It does
not install the APK, verify a physical update, or accept it for distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

_PACKAGE_LINE = re.compile(
    r"^package: name='(?P<application_id>[^']+)' "
    r"versionCode='(?P<version_code>[0-9]+)' "
    r"versionName='(?P<version_name>[^']*)'(?: .*)?$"
)
_SIGNER_FINGERPRINT = re.compile(
    r"^Signer #[0-9]+ certificate SHA-256 digest: (?P<fingerprint>[0-9a-fA-F]{64})$",
    re.MULTILINE,
)
_PLACEHOLDER_APPLICATION_IDS = {"org.example", "org.example.vpnclient"}


class VerificationError(Exception):
    """An APK could not be verified without exposing tool output or paths."""


def find_android_build_tools(sdk_root: Path | None = None) -> tuple[Path, Path]:
    """Return paths to aapt and apksigner from the newest installed build-tools."""
    roots: list[Path] = []
    if sdk_root is not None:
        roots.append(sdk_root)
    for variable in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Android" / "Sdk")

    seen_roots: set[Path] = set()
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for root in roots:
        normalized = root.expanduser().resolve()
        if normalized in seen_roots:
            continue
        seen_roots.add(normalized)
        build_tools = normalized / "build-tools"
        if not build_tools.is_dir():
            continue
        for directory in build_tools.iterdir():
            if not directory.is_dir() or not re.fullmatch(r"\d+(?:\.\d+)*", directory.name):
                continue
            version = tuple(int(part) for part in directory.name.split("."))
            candidates.append((version, directory))

    for _, directory in sorted(candidates, reverse=True):
        aapt = directory / ("aapt.exe" if os.name == "nt" else "aapt")
        apksigner_jar = directory / "lib" / "apksigner.jar"
        if aapt.is_file() and apksigner_jar.is_file():
            return aapt, apksigner_jar
    raise VerificationError("Android SDK build-tools with aapt and apksigner are unavailable")


def _find_java(java_home: Path | None = None) -> Path:
    """Find the JDK launcher used to run the SDK's apksigner JAR without cmd.exe."""
    executable = "java.exe" if os.name == "nt" else "java"
    homes = [java_home]
    for variable in ("JAVA_HOME", "JDK_HOME"):
        value = os.environ.get(variable)
        if value:
            homes.append(Path(value))
    candidates = [home / "bin" / executable for home in homes if home is not None]
    discovered = shutil.which("java")
    if discovered:
        candidates.append(Path(discovered))
    program_files = os.environ.get("ProgramFiles")
    if os.name == "nt" and program_files:
        candidates.append(
            Path(program_files) / "Android" / "Android Studio" / "jbr" / "bin" / executable
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise VerificationError("A JDK java executable is unavailable for APK signature verification")


def _run_tool(arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, shell disabled.
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError("Android SDK verification tool could not complete") from exc
    if completed.returncode != 0:
        raise VerificationError("Android SDK rejected the APK or returned an unsupported result")
    return completed.stdout


def _read_package_metadata(aapt: Path, apk: Path) -> dict[str, str | int]:
    output = _run_tool((str(aapt), "dump", "badging", str(apk)))
    first_line = output.splitlines()[0] if output.splitlines() else ""
    match = _PACKAGE_LINE.fullmatch(first_line)
    if match is None:
        raise VerificationError("APK package metadata is missing or has an unsupported format")
    return {
        "application_id": match.group("application_id"),
        "version_code": int(match.group("version_code")),
        "version_name": match.group("version_name"),
    }


def _read_signer_fingerprints(
    apksigner_jar: Path, apk: Path, java_home: Path | None = None
) -> list[str]:
    java = _find_java(java_home)
    output = _run_tool((str(java), "-jar", str(apksigner_jar), "verify", "--print-certs", str(apk)))
    fingerprints = sorted({value.lower() for value in _SIGNER_FINGERPRINT.findall(output)})
    if not fingerprints:
        raise VerificationError("APK signature verification returned no SHA-256 signer fingerprint")
    return fingerprints


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_receipt(
    apk: Path,
    expected_application_id: str,
    output: Path,
    sdk_root: Path | None = None,
    java_home: Path | None = None,
) -> dict[str, object]:
    """Verify a signed APK and write only sanitized metadata to a new JSON file."""
    is_placeholder = expected_application_id in _PLACEHOLDER_APPLICATION_IDS or any(
        component.lower() == "example" for component in expected_application_id.split(".")
    )
    if is_placeholder:
        raise VerificationError("A stable non-placeholder beta application ID is required")
    valid_application_id = re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+",
        expected_application_id,
    )
    if valid_application_id is None:
        raise VerificationError("Expected application ID is invalid")

    apk = apk.expanduser().resolve(strict=True)
    if not apk.is_file():
        raise VerificationError("APK file is unavailable")
    output = output.expanduser().resolve()
    if output.exists():
        raise VerificationError("Receipt output already exists")

    artifact_sha256 = _sha256_file(apk)
    aapt, apksigner = find_android_build_tools(sdk_root)
    metadata = _read_package_metadata(aapt, apk)
    if metadata["application_id"] != expected_application_id:
        raise VerificationError("APK application ID does not match the expected beta ID")
    fingerprints = _read_signer_fingerprints(apksigner, apk, java_home=java_home)
    if _sha256_file(apk) != artifact_sha256:
        raise VerificationError("APK changed while verification was in progress")

    receipt: dict[str, object] = {
        "observed_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "scope": "local signed APK metadata and signature verification",
        **metadata,
        "artifact_sha256": artifact_sha256,
        "signer_certificate_sha256": fingerprints,
        "verification": {
            "aapt_metadata": "passed",
            "apksigner_signature": "passed",
        },
        "update_install": "not_verified",
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
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--expected-application-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sdk-root", type=Path)
    parser.add_argument("--java-home", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        create_receipt(
            args.apk,
            args.expected_application_id,
            args.output,
            sdk_root=args.sdk_root,
            java_home=args.java_home,
        )
    except (OSError, VerificationError) as exc:
        print(f"APK verification failed: {exc}", file=sys.stderr)
        return 1
    print("Signed APK verified; sanitized receipt created. Device update remains unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

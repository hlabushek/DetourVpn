"""Deterministic tests for the unprovisioned beta-node bundle builder."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts import package_beta_node as package


def fixture_sources(root: Path, artifacts: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for source, _destination in package.SOURCE_FILES:
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{source}\n", encoding="utf-8")
    artifacts.mkdir()
    hashes = {}
    for name in package.EXPECTED_BINARIES:
        data = f"synthetic TEST ONLY binary:{name}".encode()
        (artifacts / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(package, "EXPECTED_BINARIES", hashes)


def test_package_contains_only_allowlisted_sources_and_synthetic_binaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, artifacts = tmp_path / "repo", tmp_path / "artifacts"
    repository.mkdir()
    fixture_sources(repository, artifacts, monkeypatch)
    (repository / ".env").write_text("SECRET=TEST ONLY\n", encoding="utf-8")
    (repository / ".tgcloud").mkdir()
    (repository / ".tgcloud" / "credentials").write_text("TEST ONLY\n", encoding="utf-8")

    output = tmp_path / "package"
    manifest = package.build_package(repository, artifacts, output)
    packaged = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    expected = {destination for _source, destination in package.SOURCE_FILES}
    expected |= {f"bin/{name}" for name in package.EXPECTED_BINARIES}
    expected |= {"manifest.json", "SHA256SUMS"}
    assert packaged == expected
    assert manifest["deployable"] is False
    assert ".env" not in packaged and ".tgcloud/credentials" not in packaged
    assert "node credential" in manifest["excludes"]
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0
    for entry in manifest["files"]:
        data = (output / entry["path"]).read_bytes()
        assert len(data) == entry["size"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]


def test_package_refuses_modified_pinned_binary_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, artifacts = tmp_path / "repo", tmp_path / "artifacts"
    repository.mkdir()
    fixture_sources(repository, artifacts, monkeypatch)
    (artifacts / "awg").write_bytes(b"modified synthetic TEST ONLY")
    output = tmp_path / "package"
    with pytest.raises(package.PackageError, match="pinned_node_artifact_mismatch"):
        package.build_package(repository, artifacts, output)
    assert not output.exists()


def test_package_refuses_existing_output_and_symlink_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, artifacts = tmp_path / "repo", tmp_path / "artifacts"
    repository.mkdir()
    fixture_sources(repository, artifacts, monkeypatch)
    output = tmp_path / "package"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(package.PackageError, match="package_output_already_exists"):
        package.build_package(repository, artifacts, output)
    assert marker.read_text(encoding="utf-8") == "preserve"

    source = repository / package.SOURCE_FILES[0][0]
    target = repository / "target.py"
    source.unlink()
    target.write_text("TEST ONLY\n", encoding="utf-8")
    source.symlink_to(target)
    with pytest.raises(package.PackageError, match="package_input_not_regular"):
        package.build_package(repository, artifacts, tmp_path / "package2")


def test_manifest_is_valid_json_and_checksums_cover_every_payload_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, artifacts = tmp_path / "repo", tmp_path / "artifacts"
    repository.mkdir()
    fixture_sources(repository, artifacts, monkeypatch)
    output = tmp_path / "package"
    package.build_package(repository, artifacts, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    checksum_paths = {
        line.split("  ", 1)[1]
        for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    }
    assert checksum_paths == {entry["path"] for entry in manifest["files"]}

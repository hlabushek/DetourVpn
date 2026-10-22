"""Fake OS/command metadata tests only; no root, SSH, Docker or namespace calls."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "scripts" / "awg_local_smoke.py"
SPEC = importlib.util.spec_from_file_location("staging_acceptance", SOURCE)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


@pytest.fixture
def fake_host(monkeypatch):
    monkeypatch.setattr(smoke.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(smoke.platform, "release", lambda: "fake-linux")
    monkeypatch.setattr(smoke.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        smoke.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
        raising=False,
    )
    calls = []

    def fake_run(argv, *args, **kwargs):
        calls.append(argv)
        assert argv == [smoke.IP, "-j", "-4", "address", "show"]
        return json.dumps([{"addr_info": [{"local": "45.10.166.106"}]}])

    monkeypatch.setattr(smoke, "run", fake_run)
    return calls


def test_explicit_approved_staging_address_is_read_only_validated(fake_host):
    assert smoke.require_environment("45.10.166.106") == "approved_staging_isolated_sandbox"
    assert len(fake_host) == 1


@pytest.mark.parametrize("address", [None, "127.0.0.1", "45.10.166.107", ""])
def test_implicit_or_wrong_target_refused_before_probe(fake_host, address):
    with pytest.raises(smoke.SmokeError):
        smoke.require_environment(address)
    assert fake_host == []


def test_non_root_refused_before_probe(fake_host, monkeypatch):
    monkeypatch.setattr(smoke.os, "geteuid", lambda: 1000)
    with pytest.raises(smoke.SmokeError, match="linux_root_required"):
        smoke.require_environment("45.10.166.106")
    assert fake_host == []


def test_staging_flag_does_not_accept_wsl(fake_host, monkeypatch):
    monkeypatch.setattr(smoke.platform, "release", lambda: "fake-microsoft-wsl")
    with pytest.raises(smoke.SmokeError):
        smoke.require_environment("45.10.166.106")
    assert smoke.require_environment(None) == "local_wsl_only"
    assert fake_host == []


@pytest.mark.parametrize(
    "release", [{"ID": "debian", "VERSION_ID": "24.04"}, {"ID": "ubuntu", "VERSION_ID": "22.04"}]
)
def test_unsupported_os_refused(fake_host, monkeypatch, release):
    monkeypatch.setattr(smoke.platform, "freedesktop_os_release", lambda: release)
    with pytest.raises(smoke.SmokeError):
        smoke.require_environment("45.10.166.106")
    assert fake_host == []


def test_missing_assigned_address_refused(fake_host, monkeypatch):
    monkeypatch.setattr(smoke, "run", lambda *args: "[]")
    with pytest.raises(smoke.SmokeError, match="staging_address_not_assigned"):
        smoke.require_environment("45.10.166.106")


def test_snapshot_rejects_stopped_existing_container(monkeypatch):
    monkeypatch.setattr(
        smoke, "run", lambda argv: "fake-id 10 false fake-start\nfake-id 11 true fake-start\n"
    )
    with pytest.raises(smoke.SmokeError, match="existing_containers_not_running"):
        smoke.staging_snapshot()


def test_snapshot_only_returns_hashes(monkeypatch):
    def fake_run(argv):
        if argv[0].endswith("docker"):
            return "fake-id 10 true fake-start\nfake-id 11 true fake-start\n"
        if "link" in argv:
            return '[{"ifname":"fake-interface"}]'
        if "route" in argv:
            return '[{"dst":"192.0.2.0/24","dev":"fake-interface"}]'
        return "fake-operational-metadata"

    monkeypatch.setattr(smoke, "run", fake_run)
    result = smoke.staging_snapshot()
    assert len(result) == 6
    assert all(len(value) == 64 for value in result.values())
    assert "fake" not in json.dumps(result)


@pytest.fixture
def fake_artifacts(tmp_path, monkeypatch):
    """Non-executable synthetic bytes; tests never run these fake artifacts."""
    source, target = tmp_path / "source", tmp_path / "executable-temp"
    source.mkdir()
    target.mkdir()
    payload = b"synthetic public artifact bytes"
    (source / "amneziawg-go").write_bytes(payload)
    monkeypatch.setattr(smoke, "HASHES", {"amneziawg-go": hashlib.sha256(payload).hexdigest()})
    return source, target


def test_pinned_public_binary_uses_separate_executable_directory(fake_artifacts, monkeypatch):
    source, target = fake_artifacts
    monkeypatch.setattr(smoke.os, "access", lambda path, mode: True)
    binaries = smoke.prepare_executables(source, target)
    assert Path(binaries["amneziawg-go"]).parent == target
    assert (target / "amneziawg-go").read_bytes() == (source / "amneziawg-go").read_bytes()


def test_noexec_storage_refused_before_namespace_setup(fake_artifacts, monkeypatch):
    source, target = fake_artifacts
    monkeypatch.setattr(smoke.os, "access", lambda path, mode: False)
    with pytest.raises(smoke.SmokeError, match="executable_storage_is_noexec"):
        smoke.prepare_executables(source, target)


def test_modified_public_binary_refused(fake_artifacts, monkeypatch):
    source, target = fake_artifacts
    (source / "amneziawg-go").write_bytes(b"modified synthetic bytes")
    with pytest.raises(smoke.SmokeError, match="artifact_hash_mismatch"):
        smoke.prepare_executables(source, target)

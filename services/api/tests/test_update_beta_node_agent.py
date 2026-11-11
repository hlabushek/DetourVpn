"""Node agent update bundle. TEST ONLY fake git blobs; no SSH or node is touched."""

import hashlib
import io
import subprocess
import tarfile

import pytest
from scripts import update_beta_node_agent as update


def fake_blob(rev: str, path: str) -> bytes:
    return f"{rev}:{path}\n".encode()


def test_bundle_lists_baselines_new_files_and_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update, "git_blob", fake_blob)
    archive = tarfile.open(fileobj=io.BytesIO(update.build("new", "old", True)))
    names = set(archive.getnames())
    assert {"replaced", "added", "baseline", "uploaded", "files/probe.service"} <= names
    assert "files/vpn_api_tunnels_window_runtime.py" in names
    assert "files/window_pool.py" in names and "files/latency_probe.py" in names
    read = {n: archive.extractfile(n).read().decode() for n in ("baseline", "replaced", "added")}  # type: ignore[union-attr]
    baseline = hashlib.sha256(fake_blob("old", update.REPLACED["window_gate.py"])).hexdigest()
    assert f"{baseline} window_gate.py\n" in read["baseline"]
    assert "window_pool.py" in read["added"] and "window_pool.py" not in read["replaced"]
    # Keys, configs, slots, credentials and the journal are never part of an update.
    for forbidden in ("slots.json", "agent.json", "credential", "awg/", "state.sqlite"):
        assert not any(forbidden in name for name in names)
    without = tarfile.open(fileobj=io.BytesIO(update.build("new", "old", False)))
    assert "files/probe.service" not in without.getnames()


def test_remote_programs_are_valid_shell() -> None:
    for program in (update.REMOTE, update.HASHES):
        subprocess.run(["bash", "-n"], input=program.encode(), check=True)  # noqa: S603, S607

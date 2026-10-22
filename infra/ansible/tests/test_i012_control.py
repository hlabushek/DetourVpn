"""Security-negative controller tests with explicit fake root/systemd. No SSH or sudo."""

import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "i012_control_test_target", ROOT / "scripts/i012_control.py"
)
assert spec is not None and spec.loader is not None
control: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class FakeRoot:
    """Tests only. Never executes commands, reads real root files or authenticates sudo."""

    HOST = "45.10.166.106"

    def __init__(self, tmp_path: Path, timer_fails: bool) -> None:
        self.LEASE = tmp_path / "lease"
        self.LEASE.write_text("synthetic metadata")
        self.timer_fails = timer_fails

    def trusted(self, path: Path) -> None:
        pass

    def command(self, argv: list[str], timeout: int = 30) -> str:
        if argv[0] == "/usr/sbin/ip":
            return json.dumps([{"addr_info": [{"local": self.HOST}]}])
        if self.timer_fails:
            raise RuntimeError("injected timer failure")
        return ""


@pytest.mark.parametrize("timer_fails", [False, True])
def test_expire_always_removes_grant_even_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timer_fails: bool
) -> None:
    fake = FakeRoot(tmp_path, timer_fails)
    state = tmp_path / "state"
    state.mkdir()
    for name in ("config.json", "journal.json", "lock"):
        (state / name).write_text("test only")
    sudoers = tmp_path / "sudoers"
    sudoers.write_text("synthetic grant")
    monkeypatch.setattr(control, "STATE", state)
    monkeypatch.setattr(control, "SUDOERS", sudoers)
    monkeypatch.setattr(control, "legacy", lambda: fake)
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["i012_control.py", "expire"])

    def stop(_: object) -> None:
        raise RuntimeError("injected stop failure")

    monkeypatch.setattr(control, "stop", stop)
    with pytest.raises(RuntimeError):
        control.main()
    assert not sudoers.exists() and not fake.LEASE.exists()
    assert not any(state.iterdir())


@pytest.mark.parametrize(
    "argv",
    [
        ["i012_control.py", "shell"],
        ["i012_control.py", "start", "unapproved-argument"],
        ["i012_control.py"],
    ],
)
def test_unknown_operations_never_import_privileged_code(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", argv)

    def forbidden() -> None:
        raise AssertionError("privileged code must not be imported")

    monkeypatch.setattr(control, "legacy", forbidden)
    with pytest.raises(ValueError, match="control_refused"):
        control.main()


def test_changed_agent_code_invalidates_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    metadata: dict[str, object] = {"i012_sha256": {}}
    hashes: dict[str, str] = {}
    for name in control.EXPECTED:
        target = (units if name.endswith(".service") else sources) / name
        target.write_text("reviewed test artifact")
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    metadata["i012_sha256"] = hashes

    class FakeAuthorized:
        def authorized(self) -> dict[str, object]:
            return metadata

        def trusted(self, path: Path) -> None:
            pass

    original_path = Path
    monkeypatch.setattr(control, "ROOT", sources)
    monkeypatch.setattr(
        control,
        "Path",
        lambda value: units if value == "/etc/systemd/system" else original_path(value),
    )
    assert control.validate(FakeAuthorized()) == metadata
    (sources / "i012_agent.py").write_text("changed after approval")
    with pytest.raises(ValueError, match="control_code_changed"):
        control.validate(FakeAuthorized())


@pytest.mark.parametrize(
    ("mode", "owner", "accepted"),
    [
        (stat.S_IFDIR | 0o700, 0, True),
        (stat.S_IFDIR | 0o755, 0, True),
        (stat.S_IFDIR | 0o777, 0, False),
        (stat.S_IFDIR | 0o700, 1000, False),
        (stat.S_IFREG | 0o600, 0, False),
        (stat.S_IFLNK | 0o700, 0, False),
    ],
)
def test_runtime_directory_type_owner_and_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int, owner: int, accepted: bool
) -> None:
    def fake_lstat(path: Path) -> os.stat_result:
        selected = mode if path == tmp_path else stat.S_IFDIR | 0o755
        uid = owner if path == tmp_path else 0
        # Windows stat_result normalizes uid to zero; preserve explicit Linux test metadata.
        return cast(os.stat_result, SimpleNamespace(st_mode=selected, st_uid=uid))

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    if accepted:
        control.trusted_directory(tmp_path)
    else:
        with pytest.raises(ValueError, match="untrusted_directory"):
            control.trusted_directory(tmp_path)


def test_prepare_validates_runtime_as_directory_not_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "agent"
    state.mkdir()
    old_state = tmp_path / "endpoint"
    old_state.mkdir()

    class FakePrepare(FakeRoot):
        STATE = old_state

        def trusted(self, path: Path) -> None:
            # Mirrors the real legacy helper's file-only contract.
            if path.is_dir():
                raise ValueError("file_required")

        def service_state(self) -> str:
            return "inactive"

        def set_public_key(self, text: str) -> None:
            assert text == "test-only-public-key"

    fake = FakePrepare(tmp_path, False)
    checked: list[Path] = []
    monkeypatch.setattr(control, "STATE", state)
    monkeypatch.setattr(control, "legacy", lambda: fake)
    monkeypatch.setattr(control, "validate", lambda _: {})
    monkeypatch.setattr(control, "trusted_directory", checked.append)
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["i012_control.py", "prepare"])
    cfg = {
        "node_id": "00000000-0000-4000-8000-000000000001",
        "runtime_id": "00000000-0000-4000-8000-000000000002",
        "credential": "A" * 43,  # Synthetic test credential only.
        "bootstrap_public_key": "test-only-public-key",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(cfg)))
    assert control.main() == 0
    assert checked == [state]
    assert json.loads((state / "config.json").read_text()) == {
        key: cfg[key] for key in ("node_id", "runtime_id", "credential")
    }

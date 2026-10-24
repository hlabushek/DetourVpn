"""Operator input/fencing negatives. No root/network actions, no VPN evidence."""

import importlib.util
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def operator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_i028_operator", ROOT / "infra/staging-v3/runtime.py"
    )
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG_DIR = tmp_path
    module.status = lambda: {"prepared": True, "remaining_seconds": 0}
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    return module


def test_only_fixed_slot_numbers(operator: Any) -> None:
    for invalid in (-1, 0, 3, True, "1", "1;id"):
        with pytest.raises(operator.Failure):
            operator.addresses(invalid)


@pytest.mark.parametrize(
    ("state", "code", "expected"),
    [("inactive", 0, True), ("failed", 0, True), ("active", 0, False), ("", 1, False)],
)
def test_timer_cleanup_checks_property_without_losing_inactive_exit_status(
    operator: Any, monkeypatch: pytest.MonkeyPatch, state: str, code: int, expected: bool
) -> None:
    timer = "detourvpn-i028-timeout.timer"

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args == ["/usr/bin/systemctl", "stop", timer]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["/usr/bin/systemctl", "is-active", timer]:
            # Real systemctl contract: inactive is output with a nonzero status.
            return subprocess.CompletedProcess(args, 3, "inactive\n", "")
        assert args == ["/usr/bin/systemctl", "show", "--property=ActiveState", "--value", timer]
        return subprocess.CompletedProcess(args, code, state + "\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert operator.disarm_timeout() is expected


def test_forbidden_config_fields_and_bounded_input(operator: Any, tmp_path: Path) -> None:
    for value in (
        {"node_id": str(uuid4()), "credential": secrets.token_urlsafe(32), "shell": "id"},
        {"node_id": "invalid", "credential": secrets.token_urlsafe(32)},
        {"node_id": str(uuid4()), "credential": "not-a-real-node-credential"},
    ):
        with pytest.raises((operator.Failure, ValueError)):
            operator.configure(json.dumps(value).encode())
    with pytest.raises(operator.Failure):
        operator.configure(b"x" * 513)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(
    os.name != "posix", reason="Real POSIX O_NOFOLLOW checked in isolated Linux lab"
)
def test_identity_is_immutable_and_exact_retry_is_idempotent(operator: Any, tmp_path: Path) -> None:
    request = {"node_id": str(uuid4()), "credential": secrets.token_urlsafe(32)}
    operator.configure(json.dumps(request).encode())
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    operator.configure(json.dumps(request).encode())
    with pytest.raises(operator.Failure):
        operator.configure(
            json.dumps({**request, "credential": secrets.token_urlsafe(32)}).encode()
        )
    assert before == {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    config = json.loads((tmp_path / "agent.json").read_text())
    assert config["api_origin"] == "https://localhost" and config["slot_count"] == 2


def test_expired_budget_and_unknown_cli_cannot_launch_processes(
    operator: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Expired operator attempted an OS command")

    operator.unit = forbidden
    operator.call = forbidden
    for argv in (["helper", "start-agent"], ["helper", "shell"], ["helper", "status", "extra"]):
        monkeypatch.setattr(sys, "argv", argv)
        assert operator.main() == 1


@pytest.mark.skipif(
    os.name != "posix", reason="Real POSIX O_NOFOLLOW checked in isolated Linux lab"
)
def test_no_overwrite_or_symlink_follow(operator: Any, tmp_path: Path) -> None:
    path = tmp_path / "secret"
    operator.write_private(path, "TEST ONLY FIRST")
    with pytest.raises(FileExistsError):
        operator.write_private(path, "TEST ONLY REPLACEMENT")
    assert path.read_text() == "TEST ONLY FIRST"

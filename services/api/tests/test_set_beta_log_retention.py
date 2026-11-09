# ruff: noqa: S607
"""Journald retention tool. TEST ONLY: no host is contacted."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts import set_beta_log_retention as tool


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_remote_program_is_valid_shell() -> None:
    result = subprocess.run(
        ["bash", "-n"], input=tool.REMOTE_PROGRAM.encode(), capture_output=True, check=False
    )  # noqa: S607
    assert result.returncode == 0, result.stderr


def test_program_never_deletes_without_the_vacuum_flag() -> None:
    program = tool.REMOTE_PROGRAM
    assert program.count("--vacuum-time") == 1
    assert 'if [ "$vacuum" = 1 ]' in program and "rm " not in program


def fake_run(output: str, code: int = 0) -> Any:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, code, output.encode(), b"")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_run_host_keeps_only_fixed_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = fake_run("dropin=absent\ndisk=1.2G\nnoise 9.9.9.9\nbad=1\n")
    monkeypatch.setattr(subprocess, "run", runner)
    values = tool.run_host("9.9.9.9", Path("k"), 22, "check", False)
    assert values == {"dropin": "absent", "disk": "1.2G"}
    assert runner.calls[0][-3:] == ["--", "check", "0"]


def test_failure_is_a_fixed_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", fake_run("secret", 3))
    assert tool.run_host("9.9.9.9", Path("k"), 22, "apply", False) == {"error": "remote_failed_3"}


def test_vacuum_needs_execute(capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["--vacuum"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "vacuum_requires_execute"

"""TEST ONLY (M9, ADR-175): host identifiers are not committed in the two operator scripts.

No command here touches a host: the restore script is run only to see that it refuses to start
without its operator file.
"""

import os
import re
import shutil
import subprocess  # noqa: S404 - fixed interpreter and script in one test
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# Real addresses and the managed database host name are never written in the repository.
# Loopback is allowed: it is not a host identifier.
IDENTIFIER = re.compile(
    r"\b(?!127\.0\.0\.1\b)\d{1,3}(?:\.\d{1,3}){3}\b|ep-[a-z0-9-]+\.[a-z0-9.-]*neon\.tech"
)
SCRIPTS = ("scripts/restore_primary_api.sh", "scripts/activate_beta_caddy_allowlist.ps1")


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_host_identifier_is_committed(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert IDENTIFIER.findall(text) == []


def test_restore_script_reads_host_values_from_its_operator_file() -> None:
    text = (ROOT / "scripts/restore_primary_api.sh").read_text(encoding="utf-8")
    assert 'RESTORE_ENV="${RESTORE_ENV:-/root/detourvpn-restore.env}"' in text
    assert "DATABASE_HOST" in text and "PUBLIC_IP" in text


def test_powershell_reads_host_values_from_its_operator_file() -> None:
    text = (ROOT / "scripts/activate_beta_caddy_allowlist.ps1").read_text(encoding="utf-8")
    assert "beta-operator.env" in text
    for key in ("BETA_HOST", "BETA_NODE_IP", "BETA_SSH_KEY"):
        assert key in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_restore_script_refuses_to_start_without_its_operator_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.env"
    # Git bash explicitly: CreateProcess would find the WSL launcher in System32 first.
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603 - fixed script; the env only changes RESTORE_ENV
        [bash, str(ROOT / "scripts/restore_primary_api.sh")],
        env={**os.environ, "RESTORE_ENV": str(missing)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing" in result.stdout + result.stderr

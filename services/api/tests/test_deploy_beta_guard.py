"""TEST ONLY (M3, ADR-176): the beta deploy refuses a primary whose API reads the managed database.

Pure text checks of the generated remote program and of its guard regex; no host is contacted.
"""

import re
import shutil
import subprocess  # noqa: S404 - fixed bash for a syntax check only
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = (ROOT / "scripts/deploy_beta_api_latest.py").read_text(encoding="utf-8")
GUARD = re.search(r"grep -Eq '([^']+)' /etc/detourvpn-beta/api\.env", SOURCE)


def guard() -> re.Pattern[str]:
    assert GUARD is not None
    return re.compile(GUARD.group(1))


LOCAL = 'VPN_DATABASE_URL="postgresql+psycopg://vpn_api:x@127.0.0.1:25433/detourvpn_beta"'
LOCAL_UNQUOTED = "VPN_DATABASE_URL=postgresql+psycopg://vpn_api:x@127.0.0.1:25433/detourvpn_beta"
MANAGED = (
    'VPN_DATABASE_URL="postgresql+psycopg://vpn_api:x@db.example.invalid/neondb'
    '?sslmode=verify-full"'
)


def test_guard_accepts_only_the_local_docker_database() -> None:
    assert guard().search(LOCAL)
    assert guard().search(LOCAL_UNQUOTED)
    assert not guard().search(MANAGED)
    assert not guard().search(LOCAL.replace("25433", "25434"))
    assert not guard().search(LOCAL.replace("127.0.0.1", "db.example.invalid"))


def test_refusal_happens_before_any_change() -> None:
    body = SOURCE[SOURCE.index('SCRIPT = r"""') :]
    refusal = body.index("refused=database_not_local_docker")
    # The first change of the host is the phase that replaces the code; nothing runs before it
    # that alters the host, and the refusal comes before it.
    assert refusal < body.index("phase=replace_and_migrate")
    assert refusal < body.index("pg_dump")


def test_generated_remote_program_is_valid_bash(tmp_path: Path) -> None:
    body = SOURCE[SOURCE.index('SCRIPT = r"""') + len('SCRIPT = r"""') :]
    body = body[: body.index('"""')]
    for key in (
        "__EXPECTED_HOST__",
        "__BEFORE__",
        "__AFTER__",
        "__NEW_NAMES__",
        "__NEWFILES__",
        "__BASELINES__",
        "__UPLOADED__",
        "__INSTALLED__",
        "__CHANGED__",
    ):
        body = body.replace(key, "x")
    script = tmp_path / "remote.sh"
    script.write_bytes(body.encode("utf-8"))
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603 - bash -n on a local file only
        [bash, "-n", script.as_posix()], capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")

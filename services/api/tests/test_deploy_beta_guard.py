"""TEST ONLY (M3, ADR-176): the beta deploy refuses a primary whose API reads the managed database.

Pure text checks of the generated remote program and of its guard regex; no host is contacted.
"""

import importlib.util
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


def remote_body(mode: str) -> str:
    body = SOURCE[SOURCE.index('SCRIPT = r"""') + len('SCRIPT = r"""') :]
    body = body[: body.index('"""')]
    return (
        body.replace("__DB_MODE__", mode)
        .replace("__HELPER_NAME__", "managed_db_step.py")
        .replace("__HELPER_SHA__", "0" * 64)
    )


def test_generated_remote_program_is_valid_bash(tmp_path: Path) -> None:
    body = remote_body("local")
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


# ADR-197: managed-database mode. TEST ONLY; no host or database is contacted.
MANAGED_GUARD = re.search(
    r"grep -Eq '([^']+)' /etc/detourvpn-beta/api\.env \|\| \{ echo refused=database_not_managed",
    SOURCE,
)


def managed_guard() -> re.Pattern[str]:
    assert MANAGED_GUARD is not None
    return re.compile(MANAGED_GUARD.group(1))


def test_managed_guard_accepts_only_a_verify_full_dns_host() -> None:
    assert managed_guard().search(MANAGED)
    assert managed_guard().search(MANAGED.replace('"', ""))
    assert managed_guard().search(MANAGED.replace('full"', 'full&channel_binding=require"'))
    assert managed_guard().search(MANAGED.replace(".invalid/", ".invalid:5432/"))
    assert not managed_guard().search(LOCAL)
    assert not managed_guard().search(LOCAL_UNQUOTED)
    assert not managed_guard().search(MANAGED.replace("verify-full", "require"))
    assert not managed_guard().search(MANAGED.replace("db.example.invalid", "10.0.0.5"))
    assert not managed_guard().search(MANAGED.replace("db.example.invalid", "localhost"))
    assert not managed_guard().search(MANAGED.replace('full"', 'full&sslrootcert=x"'))
    # The local guard is still the first grep and still refuses the managed URL.
    assert guard().pattern != managed_guard().pattern
    assert not guard().search(MANAGED)


def test_managed_refusals_come_before_any_change() -> None:
    body = SOURCE[SOURCE.index('SCRIPT = r"""') :]
    first_change = body.index("phase=backup")
    for token in (
        "refused=database_not_managed",
        "refused=database_url_ambiguous",
        "refused=managed_role_cannot_migrate",
        "refused=unknown_database_mode",
        "__HELPER_SHA__",
    ):
        assert body.index(token) < first_change, token
    assert body.index("refused=database_not_local_docker") < body.index("pg_dump")


def test_managed_remote_program_is_valid_bash(tmp_path: Path) -> None:
    body = remote_body("managed")
    for key in ("__EXPECTED_HOST__", "__BEFORE__", "__AFTER__", "__NEW_NAMES__", "__NEWFILES__",
                "__BASELINES__", "__UPLOADED__", "__INSTALLED__", "__CHANGED__"):  # fmt: skip
        body = body.replace(key, "x")
    script = tmp_path / "remote.sh"
    script.write_bytes(body.encode("utf-8"))
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603 - bash -n on a local file only
        [bash, "-n", script.as_posix()], capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def _load(name: str, path: Path, monkeypatch) -> object:  # noqa: ANN001
    monkeypatch.setenv("USERPROFILE", str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mode_arguments_require_a_snapshot_ref_for_managed_only(monkeypatch) -> None:  # noqa: ANN001
    path = ROOT / "scripts/deploy_beta_api_latest.py"
    deploy = _load("deploy_beta_api_latest", path, monkeypatch)
    assert deploy.mode_error("local", None) is None
    assert deploy.mode_error("local", "br-abc-123").startswith("refused=snapshot_ref_only")
    assert deploy.mode_error("managed", None).startswith("refused=snapshot_ref_required")
    assert deploy.mode_error("managed", "br-cool-sky-123456") is None
    for bad in ("x", "postgresql://u:p@h/d", "a b c", "../etc", "-abc", "id;rm"):
        assert deploy.mode_error("managed", bad).startswith("refused=snapshot_ref_bad_shape"), bad


def test_helper_checks_arguments_and_never_prints_exception_text(
    monkeypatch,
    tmp_path: Path,  # noqa: ANN001
) -> None:
    helper = _load("managed_db_step", ROOT / "scripts/managed_db_step.py", monkeypatch)
    assert helper.valid_target("0029_window_hot_path_indexes")
    assert not helper.valid_target("head")
    assert not helper.valid_target("0029; DROP")
    assert helper.run([], tmp_path) == (2, "managed_db_step_usage")
    assert helper.run(["upgrade", "head"], tmp_path) == (2, "managed_db_step_bad_target")
    assert helper.run(["revision"], tmp_path) == (2, "managed_db_step_wrong_directory")

    (tmp_path / "alembic.ini").write_text("", encoding="utf-8")
    fake_url = "postgresql+psycopg://u:SECRET@db.example.invalid/x"  # TEST ONLY placeholder

    def boom(_root: Path) -> None:
        raise RuntimeError(fake_url)

    monkeypatch.setattr(helper, "_engine", boom)
    code, line = helper.run(["revision"], tmp_path)
    assert code == 1
    assert line == "managed_migration_failed stage=connect error=RuntimeError"
    assert "SECRET" not in line

"""TEST ONLY (M10, ADR-174): the Neon migration never puts a password on a command line.

Pure parsing and a temporary pgpass file; no database, no network. Synthetic values only.
"""

import os
import re
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts import pg_conninfo  # noqa: E402

PASSWORD = "TEST_ONLY:pa\\ss'word"  # noqa: S105 - synthetic test value
URL = (
    "postgresql+psycopg://vpn_api:TEST_ONLY%3Apa%5Css%27word@db.example.invalid:5433/neondb"
    "?sslmode=verify-full&channel_binding=require"
)


def test_conninfo_has_everything_but_the_password() -> None:
    params, password = pg_conninfo.split_url(URL)
    assert password == PASSWORD
    info = pg_conninfo.conninfo(params)
    assert "TEST_ONLY" not in info and "password" not in info
    assert info == (
        "host='db.example.invalid' port='5433' user='vpn_api' dbname='neondb' "
        "sslmode='verify-full' channel_binding='require'"
    )


def test_pgpass_line_escapes_separators() -> None:
    params, password = pg_conninfo.split_url(URL)
    line = pg_conninfo.pgpass_line(params, password)
    assert line == "db.example.invalid:5433:neondb:vpn_api:TEST_ONLY\\:pa\\\\ss'word\n"


@pytest.mark.parametrize(
    "url",
    [
        "mysql://vpn_api:x@db.example.invalid/neondb",
        "postgresql://vpn_api@db.example.invalid/neondb",  # no password
        "postgresql://vpn_api:x@db.example.invalid/",  # no database
        "postgresql://vpn_api:x@db.example.invalid/neondb?hostaddr=192.0.2.1",
        "postgresql://vpn_api:x@db.example.invalid/neondb?options=-c%20x",
    ],
)
def test_unusable_urls_are_refused_with_fixed_codes(url: str) -> None:
    with pytest.raises(pg_conninfo.ConninfoError) as error:
        pg_conninfo.split_url(url)
    assert re.fullmatch(r"database_url_[a-z_]+", str(error.value))


def test_cli_prints_no_secret_and_writes_a_private_pgpass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TARGET_DATABASE_URL", URL)
    target = tmp_path / "pgpass"
    assert pg_conninfo.main(["TARGET_DATABASE_URL", str(target)]) == 0
    out, err = capsys.readouterr()
    assert "TEST_ONLY" not in out + err and out.startswith("host='db.example.invalid'")
    assert target.read_text(encoding="utf-8").endswith(":TEST_ONLY\\:pa\\\\ss'word\n")
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_migration_script_passes_no_url_to_postgres_tools() -> None:
    script = (ROOT / "scripts/migrate_db_to_neon.sh").read_text(encoding="utf-8")
    commands = [
        line
        for line in script.splitlines()
        if re.match(r"\s*(pg_dump|pg_restore|psql|count_tables)\b", line)
        or line.lstrip().startswith("--dbname")
    ]
    assert commands
    assert not any("DATABASE_URL" in line for line in commands)
    assert 'export PGPASSFILE="${SECRETS_DIR}/pgpass"' in script
    assert "unset SOURCE_DATABASE_URL TARGET_DATABASE_URL" in script

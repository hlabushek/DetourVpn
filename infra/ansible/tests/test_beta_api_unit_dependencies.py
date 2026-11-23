"""TEST ONLY (M4, ADR-177): the beta API unit does not hard-require the local database unit."""

import re
from pathlib import Path

BETA = Path(__file__).resolve().parents[2] / "beta"


def directives(name: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in (BETA / name).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z]+)=(.*)", line.strip())
        if match:
            result.setdefault(match.group(1), []).append(match.group(2))
    return result


def test_api_unit_does_not_require_the_local_database_unit() -> None:
    unit = directives("detourvpn-beta-api.service")
    assert not any("detourvpn-beta-database.service" in value for value in unit.get("Requires", []))
    assert not any("detourvpn-beta-database.service" in value for value in unit.get("BindsTo", []))
    assert any("detourvpn-beta-database.service" in value for value in unit.get("Wants", []))
    assert any("detourvpn-beta-database.service" in value for value in unit.get("After", []))


def test_api_unit_keeps_its_restart_policy() -> None:
    unit = directives("detourvpn-beta-api.service")
    assert unit.get("Restart") == ["on-failure"]
    assert unit.get("RestartSec") == ["5"]

"""SYNTHETIC control responses only; not staging cleanup evidence."""

from pathlib import Path
from typing import Any

import pytest
import test_i019_live as live


@pytest.mark.parametrize(
    ("session", "passed", "diagnostic", "expected"),
    [
        (True, True, False, False),
        (True, False, False, True),
        (True, True, True, True),
        (False, False, True, False),
        (False, True, True, False),
    ],
)
def test_only_full_success_auto_closes_diagnostic_session(
    session: bool,
    passed: bool,
    diagnostic: bool,
    expected: bool,
) -> None:
    assert (
        live.retain_session_grants(
            session=session,
            passed=passed,
            diagnostic_only=diagnostic,
        )
        is expected
    )


@pytest.mark.parametrize("retain", [True, False])
def test_attempt_stops_runtime_without_renewing_or_removing_session_grant(
    monkeypatch: pytest.MonkeyPatch,
    retain: bool,
) -> None:
    calls: list[str] = []

    def remote(host: str, action: str) -> dict[str, Any]:
        assert host == live.HOSTS[1]
        calls.append(action)
        if action == "status":
            return {"endpoint_active": False, "agent_active": False}
        return {
            "cleanup_complete": True,
            "forwarding_restored": True,
            "owned_resources_absent": True,
            "control_grant_removed": not retain,
        }

    monkeypatch.setattr(live, "remote", remote)
    result = live.finish_node_attempt(live.HOSTS[1], retain_grant=retain)
    assert calls == (["stop", "status"] if retain else ["expire"])
    assert result.get("control_grant_retained_until_original_deadline", False) == retain


@pytest.mark.parametrize(
    "bad", ["cleanup_complete", "forwarding_restored", "owned_resources_absent"]
)
def test_failed_cleanup_never_confirms_reusable_session(
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    result = {
        key: True
        for key in (
            "cleanup_complete",
            "forwarding_restored",
            "owned_resources_absent",
        )
    }
    result[bad] = False
    monkeypatch.setattr(live, "remote", lambda *_: result)
    with pytest.raises(RuntimeError, match="i019_attempt_cleanup_not_confirmed"):
        live.finish_node_attempt(live.HOSTS[1], retain_grant=True)


def test_retained_grant_requires_both_services_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    def remote(_host: str, action: str) -> dict[str, Any]:
        if action == "status":
            return {"endpoint_active": False, "agent_active": True}
        return {
            key: True
            for key in (
                "cleanup_complete",
                "forwarding_restored",
                "owned_resources_absent",
            )
        }

    monkeypatch.setattr(live, "remote", remote)
    with pytest.raises(RuntimeError, match="i019_attempt_still_active"):
        live.finish_node_attempt(live.HOSTS[1], retain_grant=True)


def test_finish_attempts_both_hosts_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "docs/evidence").mkdir(parents=True)
    monkeypatch.setattr(live, "ROOT", tmp_path)
    calls = []

    def finish(host: str, *, retain_grant: bool) -> dict[str, Any]:
        assert not retain_grant
        calls.append(host)
        if host == live.HOSTS[0]:
            raise RuntimeError("TEST-SECRET-not-to-be-logged")
        return {"control_grant_removed": True}

    monkeypatch.setattr(live, "finish_node_attempt", finish)
    assert not live.finish_diagnostic_session()
    assert calls == list(live.HOSTS)
    assert "TEST-SECRET" not in (tmp_path / "docs/evidence/i-019-session-cleanup.json").read_text()

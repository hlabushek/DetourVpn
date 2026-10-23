"""TEST ONLY fake OS/commands. No SSH, sudo, firewall or real VPN evidence."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def control(monkeypatch, tmp_path):
    module = load("test_i019_control", "i019_control.py")
    monkeypatch.setattr(module, "CONTROL", tmp_path)
    monkeypatch.setattr(module, "trusted", lambda path: None)
    return module


@pytest.mark.parametrize(
    "value", [{}, {"host": "127.0.0.1"}, {"host": "85.209.132.205", "extra": 1}]
)
def test_host_config_refuses_unapproved_or_extra_fields(control, monkeypatch, tmp_path, value):
    path = tmp_path / "host.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(control, "HOST_FILE", path)
    with pytest.raises((ValueError, KeyError)):
        control.host()


@pytest.mark.parametrize("host", ["45.10.166.106", "85.209.132.205"])
def test_host_config_accepts_only_explicit_pair(control, monkeypatch, tmp_path, host):
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"host": host}))
    monkeypatch.setattr(control, "HOST_FILE", path)
    assert control.host() == host


def fake_forwarding(value):
    state = {"value": value, "writes": []}

    def command(argv):
        if argv[-1] == "net.ipv4.ip_forward":
            return state["value"]
        assert argv[:3] == ["/usr/sbin/sysctl", "-q", "-w"]
        state["value"] = argv[-1].split("=")[-1]
        state["writes"].append(state["value"])
        return ""

    return SimpleNamespace(command=command), state


@pytest.mark.parametrize("original", ["0", "1"])
def test_forwarding_is_recorded_before_mutation_and_restored(control, original):
    old, state = fake_forwarding(original)
    assert control.forwarding(old, True)
    marker = control.CONTROL / "forwarding.json"
    assert json.loads(marker.read_text()) == {"original": original}
    assert state["value"] == "1"
    with pytest.raises(FileExistsError):
        control.forwarding(old, True)
    assert control.forwarding(old, False)
    assert state["value"] == original and not marker.exists()
    assert state["writes"] == (["1", "0"] if original == "0" else [])
    assert control.forwarding(old, False)  # Repeated cleanup is harmless.


def test_forwarding_drift_does_not_blindly_override_other_operator(control):
    old, state = fake_forwarding("1")
    control.forwarding(old, True)
    state["value"] = "0"
    assert control.forwarding(old, False) is False
    assert state["writes"] == []
    assert (control.CONTROL / "forwarding.json").exists()


def test_malformed_forwarding_marker_never_reaches_write(control):
    old, state = fake_forwarding("1")
    (control.CONTROL / "forwarding.json").write_text('{"original":"2"}')
    with pytest.raises(ValueError):
        control.forwarding(old, False)
    assert state["writes"] == []


@pytest.mark.parametrize("action", ["agent", "forwarding-start", "forwarding-stop"])
def test_sudo_grant_cannot_invoke_internal_systemd_actions(control, monkeypatch, action):
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setenv("SUDO_USER", "vpn-deploy")
    monkeypatch.setattr(control, "host", lambda: "85.209.132.205")
    old = SimpleNamespace(command=lambda argv: '[{"addr_info":[{"local":"85.209.132.205"}]}]')
    monkeypatch.setattr(control, "legacy", lambda host: old)
    monkeypatch.setattr(sys, "argv", ["i019_control.py", action])
    monkeypatch.setattr(control, "forwarding", lambda *args: pytest.fail("must not mutate"))
    monkeypatch.setattr(control, "run_agent", lambda: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="internal_action"):
        control.main()


def test_unknown_action_refused_before_reading_host(control, monkeypatch):
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["i019_control.py", "sh"])
    monkeypatch.setattr(control, "host", lambda: pytest.fail("must refuse first"))
    with pytest.raises(ValueError):
        control.main()


@pytest.fixture
def window():
    smoke = load("i019_smoke", "awg_local_smoke.py")
    with patch.dict(sys.modules, {"awg_local_smoke": smoke}):
        return load("i019_window", "android_staging_window.py")


def test_i019_configuration_cannot_target_third_host_and_uses_separate_resources(window):
    with pytest.raises(window.SmokeError):
        window.configure_i019("8.8.8.8")
    assert window.I019 is False and window.PORT == "51892"
    window.configure_i019("85.209.132.205")
    assert window.I019 and window.PORT == "51893"
    assert window.NS == "vpn-i019" and window.VETH_NS == "v19ns"
    assert window.CONTAINERS == ()
    assert window.STATE == Path("/run/project-vpn-i019")
    assert all(
        "project-vpn-i011" not in window.rule_command("-D", rule)
        for rule in window.firewall_rules()
    )


@pytest.mark.parametrize("foreign", [{"rule": {}}, {"set": {}}, {"map": {}}])
def test_empty_nft_cleanup_preserves_foreign_objects(window, monkeypatch, foreign):
    window.configure_i019("85.209.132.205")
    monkeypatch.setattr(window, "i019_tables", lambda: [["ip", "filter"]])
    calls = []

    def run(argv):
        calls.append(argv)
        assert "delete" not in argv
        return json.dumps({"nftables": [foreign]})

    monkeypatch.setattr(window, "run", run)
    with pytest.raises(window.SmokeError, match="foreign_firewall_object"):
        window.cleanup_i019_tables([])
    assert len(calls) == 1


def test_existing_empty_tables_are_never_deleted(window, monkeypatch):
    window.configure_i019("85.209.132.205")
    tables = [["ip", "filter"], ["ip", "nat"]]
    monkeypatch.setattr(window, "i019_tables", lambda: tables)
    monkeypatch.setattr(
        window, "run", lambda argv: pytest.fail("existing table must not be touched")
    )
    window.cleanup_i019_tables(tables)


def test_foreign_chain_policy_refuses_table_deletion(window, monkeypatch):
    window.configure_i019("85.209.132.205")
    monkeypatch.setattr(window, "i019_tables", lambda: [["ip", "filter"]])
    chain = {
        "chain": {"name": "INPUT", "hook": "input", "type": "filter", "prio": 0, "policy": "drop"}
    }
    monkeypatch.setattr(window, "run", lambda argv: json.dumps({"nftables": [chain]}))
    with pytest.raises(window.SmokeError, match="foreign_firewall_chain"):
        window.cleanup_i019_tables([])


def test_restoration_detects_drift_even_after_unit_hook_removed_marker(control):
    old, state = fake_forwarding("0")
    control.forwarding(old, True)
    assert control.forwarding(old, False)
    state["value"] = "1"
    assert control.forwarding(old, False) is False


@pytest.mark.parametrize("fail_agent_stop", [False, True])
def test_prestart_cleanup_attempts_both_units_and_requires_confirmed_stop(
    control, monkeypatch, tmp_path, fail_agent_stop
):
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        if "--property=ActiveState" in argv:
            return "inactive"
        if "--property=InvocationID" in argv:
            return ""
        if fail_agent_stop and argv == ["/usr/bin/systemctl", "stop", control.AGENT_UNIT]:
            raise RuntimeError("TEST ONLY")
        return ""

    old = SimpleNamespace(command=command, STATE=tmp_path)
    monkeypatch.setattr(control, "forwarding", lambda *args: True)
    result = control.stop(old)
    assert ["/usr/bin/systemctl", "stop", control.UNIT] in calls
    assert result["cleanup_complete"] is (not fail_agent_stop)

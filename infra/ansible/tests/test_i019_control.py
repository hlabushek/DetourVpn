"""TEST ONLY fake OS/commands. No SSH, sudo, firewall or real VPN evidence."""

import builtins
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


def test_i019_node_agent_imports_with_python310_datetime_api(monkeypatch):
    """Ubuntu 22.04 staging uses Python 3.10, which has no datetime.UTC."""
    original_import = builtins.__import__

    def import_without_datetime_utc(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "datetime" and "UTC" in (fromlist or ()):
            raise ImportError("cannot import name 'UTC' from 'datetime'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_datetime_utc)
    spec = importlib.util.spec_from_file_location(
        "test_i019_agent_python310", ROOT / "services/node-agent/agent.py"
    )
    assert spec is not None and spec.loader is not None
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    assert agent.UTC is agent.timezone.utc
    assert agent.datetime.now(agent.UTC).utcoffset().total_seconds() == 0
    assert agent.parse_utc_datetime("2026-11-01T12:34:56Z").utcoffset().total_seconds() == 0
    assert agent.parse_utc_datetime("2026-11-01T12:34:56+00:00").utcoffset().total_seconds() == 0
    with pytest.raises(agent.AgentFault, match="invalid_command"):
        agent.parse_utc_datetime("2026-11-01T12:34:56")


def test_i019_android_egress_oracle_matches_approved_pair():
    source = (
        ROOT / "apps/android/app/src/androidTest/java/org/example/vpnpoc/I028Probe.java"
    ).read_text()
    assert 'ipv4.equals("45.10.166.106") ? "node_a"' in source
    assert 'ipv4.equals("150.241.99.227") ? "node_b"' in source
    assert "85.209.132.205" not in source


def test_agent_request_diagnostic_exposes_only_fixed_stage_and_http_status(control):
    invocation = "a" * 32
    lines = [
        json.dumps(
            {
                "error": "control_plane_request_failed",
                "stage": "poll",
                "reason": "control_plane_http_503",
                "response": "must-not-escape",
            }
        ),
        json.dumps(
            {
                "error": "control_plane_request_failed",
                "stage": "ack",
                "reason": "control_plane_http_422",
                "credential": "must-not-escape",
            }
        ),
        json.dumps(
            {
                "error": "control_plane_request_failed",
                "stage": "command",
                "reason": "peer_apply_failed",
                "exception": "must-not-escape",
            }
        ),
        json.dumps({"error": "control_plane_request_failed", "stage": "other", "reason": "secret"}),
    ]
    old = SimpleNamespace(
        command=lambda argv: invocation if "--property=InvocationID" in argv else "\n".join(lines)
    )

    assert control.agent_request_diagnostic(old) == {
        "stage": "command",
        "reason": "peer_apply_failed",
    }
    assert control.agent_request_diagnostics(old) == [
        {"stage": "poll", "reason": "control_plane_http_503"},
        {"stage": "ack", "reason": "control_plane_http_422"},
        {"stage": "command", "reason": "peer_apply_failed"},
    ]


def test_agent_request_diagnostic_rejects_unbounded_invocation_id(control):
    old = SimpleNamespace(command=lambda _argv: "not-an-invocation-id")
    assert control.agent_request_diagnostic(old) is None


@pytest.fixture
def control(monkeypatch, tmp_path):
    module = load("test_i019_control", "i019_control.py")
    monkeypatch.setattr(module, "CONTROL", tmp_path)
    monkeypatch.setattr(module, "trusted", lambda path: None)
    return module


@pytest.mark.parametrize(
    "value", [{}, {"host": "127.0.0.1"}, {"host": "150.241.99.227", "extra": 1}]
)
def test_host_config_refuses_unapproved_or_extra_fields(control, monkeypatch, tmp_path, value):
    path = tmp_path / "host.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(control, "HOST_FILE", path)
    with pytest.raises((ValueError, KeyError)):
        control.host()


@pytest.mark.parametrize("host", ["45.10.166.106", "150.241.99.227"])
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
    monkeypatch.setattr(control, "host", lambda: "150.241.99.227")
    old = SimpleNamespace(command=lambda argv: '[{"addr_info":[{"local":"150.241.99.227"}]}]')
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
    window.configure_i019("150.241.99.227")
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
    window.configure_i019("150.241.99.227")
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
    window.configure_i019("150.241.99.227")
    tables = [["ip", "filter"], ["ip", "nat"]]
    monkeypatch.setattr(window, "i019_tables", lambda: tables)
    monkeypatch.setattr(
        window, "run", lambda argv: pytest.fail("existing table must not be touched")
    )
    window.cleanup_i019_tables(tables)


def test_foreign_chain_policy_refuses_table_deletion(window, monkeypatch):
    window.configure_i019("150.241.99.227")
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


@pytest.mark.parametrize("fail_endpoint_stop", [False, True])
def test_cleanup_attempts_both_stops_after_resume_failure(
    control, monkeypatch, tmp_path, fail_endpoint_stop
):
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        if "--property=ActiveState" in argv:
            return "active"
        if "--property=InvocationID" in argv:
            return ""
        if "--signal=SIGCONT" in argv:
            raise RuntimeError("TEST ONLY")
        if fail_endpoint_stop and argv == ["/usr/bin/systemctl", "stop", control.UNIT]:
            raise RuntimeError("TEST ONLY")
        return ""

    old = SimpleNamespace(command=command, STATE=tmp_path)
    monkeypatch.setattr(control, "forwarding", lambda *args: True)
    result = control.stop(old)
    assert ["/usr/bin/systemctl", "stop", control.AGENT_UNIT] in calls
    assert ["/usr/bin/systemctl", "stop", control.UNIT] in calls
    assert result["agent_resume_failed"] is True
    assert result["agent_stop_command_ok"] is True
    assert result["endpoint_stop_command_ok"] is (not fail_endpoint_stop)
    assert result["cleanup_complete"] is (not fail_endpoint_stop)


@pytest.mark.parametrize("failure", ["agent_state", "forwarding", "receipt"])
def test_cleanup_failures_still_attempt_both_stops_and_do_not_forge_receipt(
    control, monkeypatch, tmp_path, failure
):
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        if "--property=ActiveState" in argv:
            if failure == "agent_state":
                raise RuntimeError("TEST ONLY")
            return "inactive"
        if "--property=InvocationID" in argv:
            if failure == "receipt":
                raise RuntimeError("TEST ONLY")
            return ""
        return ""

    def forwarding(*_args):
        if failure == "forwarding":
            raise RuntimeError("TEST ONLY")
        return True

    old = SimpleNamespace(command=command, STATE=tmp_path)
    monkeypatch.setattr(control, "forwarding", forwarding)
    result = control.stop(old)
    assert ["/usr/bin/systemctl", "stop", control.AGENT_UNIT] in calls
    assert ["/usr/bin/systemctl", "stop", control.UNIT] in calls
    assert result["agent_state_query_failed"] is (failure == "agent_state")
    assert result["receipt_read_failed"] is (failure == "receipt")
    assert result["cleanup_complete"] is (failure == "agent_state")


def test_cleanup_stop_timeouts_fit_systemd_limits(control, monkeypatch, tmp_path):
    stop_timeouts = {}

    def command(argv, **kwargs):
        if "--property=ActiveState" in argv:
            return "inactive"
        if "--property=InvocationID" in argv:
            return ""
        if argv[:2] == ["/usr/bin/systemctl", "stop"]:
            stop_timeouts[argv[2]] = kwargs.get("timeout")
        return ""

    old = SimpleNamespace(command=command, STATE=tmp_path)
    monkeypatch.setattr(control, "forwarding", lambda *args: True)
    assert control.stop(old)["cleanup_complete"] is True
    assert stop_timeouts == {control.AGENT_UNIT: 30, control.UNIT: 200}

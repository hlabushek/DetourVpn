"""Synthetic command/OS tests only. No SSH, real root, firewall or tunnel calls."""

import base64
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load("window_smoke", "awg_local_smoke.py")
with patch.dict(sys.modules, {"awg_local_smoke": smoke}):
    window = load("android_window", "android_staging_window.py")


@pytest.mark.parametrize(
    "key", ["", " ", "../../file", "$(id)", "A" * 44, base64.b64encode(bytes(32)).decode()]
)
def test_invalid_public_key_refused(key):
    with pytest.raises(window.SmokeError, match="invalid_public_key"):
        window.public_key(key)


def test_public_key_canonical_input_only():
    value = base64.b64encode(bytes(range(32))).decode()
    assert window.public_key(value) == value
    with pytest.raises(window.SmokeError):
        window.public_key(value + "\n")


@pytest.mark.parametrize("dst", ["10.0.0.0/8", "10.203.71.2/32", "10.203.72.0/30"])
def test_subnet_overlap_refused_in_any_routing_table(dst):
    with pytest.raises(window.SmokeError, match="subnet_collision"):
        window.check_routes([{"dst": dst, "table": 500}])


def test_unrelated_routes_and_default_are_allowed():
    window.check_routes([{"dst": "default"}, {"dst": "10.8.1.0/24"}, {"dst": "172.17.0.0/16"}])


def test_host_firewall_limits_all_accept_rules_and_nat():
    rules = window.firewall_rules()
    for table, chain, args in rules:
        if "ACCEPT" in args:
            assert table == "filter" and chain == "FORWARD"
            assert window.VETH in args and "ens3" in args
        if "DNAT" in args and chain == "PREROUTING":
            assert ["-d", window.HOST] == args[2:4]
            assert "51892" in args and "10.203.72.2:51892" in args
        if "MASQUERADE" in args:
            assert args[:4] == ["-s", "10.203.72.2/32", "-o", "ens3"]
    outgoing_accept = next(
        i for i, (_, _, args) in enumerate(rules) if args[:4] == ["-i", window.VETH, "-o", "ens3"]
    )
    for cidr in window.BLOCKED:
        assert any(
            args == ["-i", window.VETH, "-d", cidr, "-j", "DROP"]
            for _, _, args in rules[:outgoing_accept]
        )
    for rule in rules:
        command = window.rule_command("-D", rule)
        assert "project-vpn-i011" in command
        assert "-F" not in command and "--flush" not in command


def test_manual_serve_refused_before_any_commands(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(window, "run", lambda *args: pytest.fail("must refuse before command"))
    with pytest.raises(window.SmokeError, match="reviewed_systemd_unit_required"):
        window.serve()


@pytest.mark.parametrize("props", ["", "RuntimeMaxUSec=infinity", "KillMode=process"])
def test_missing_deadline_or_stop_hook_refused_before_preflight(monkeypatch, props):
    monkeypatch.setenv("INVOCATION_ID", "synthetic")
    monkeypatch.setattr(window, "run", lambda *args: props)
    monkeypatch.setattr(window, "preflight", lambda: pytest.fail("must refuse before preflight"))
    with pytest.raises(window.SmokeError, match="deadline_or_cleanup_missing"):
        window.serve()


def test_preflight_failure_never_creates_ownership_marker(monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "synthetic")
    monkeypatch.setattr(
        window,
        "run",
        lambda *args: (
            "RuntimeMaxUSec=10min\nKillMode=control-group\n"
            "Restart=no\nExecStopPost=android_staging_window.py cleanup"
        ),
    )

    def refused():
        raise window.SmokeError("synthetic_collision")

    monkeypatch.setattr(window, "preflight", refused)
    monkeypatch.setattr(window, "private_file", lambda *args: pytest.fail("no state writes"))
    with pytest.raises(window.SmokeError, match="synthetic_collision"):
        window.serve()


def test_snapshot_ignores_iptables_generation_timestamps(monkeypatch):
    timestamp = ["first"]

    def fake_run(argv):
        if argv[0].endswith("docker"):
            return "synthetic 10 true started\nsynthetic 11 true started\n"
        return "# Generated " + timestamp[0] + "\n*filter\nCOMMIT\n" if "save" in argv[0] else "[]"

    monkeypatch.setattr(window, "run", fake_run)
    first = window.snapshot()
    timestamp[0] = "second"
    assert window.snapshot() == first
    assert all(len(value) == 64 for value in first.values())


@pytest.fixture
def fake_state(tmp_path, monkeypatch):
    monkeypatch.setattr(window, "STATE", tmp_path)
    monkeypatch.setattr(window, "SOCKET", tmp_path / "synthetic.sock")
    monkeypatch.setattr(window, "trusted", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "snapshot", lambda **kwargs: {"synthetic_state": "same"})
    return tmp_path


@pytest.fixture
def revoke_environment(fake_state, monkeypatch):
    peer = base64.b64encode(bytes(range(32))).decode()
    source = fake_state / "public-key"
    source.write_text(peer)
    binary = fake_state / "awg"
    binary.write_bytes(b"synthetic pinned binary")
    monkeypatch.setattr(window, "INPUT", source)
    monkeypatch.setattr(window, "BIN", fake_state)
    monkeypatch.setattr(
        window, "HASHES", {"awg": window.hashlib.sha256(binary.read_bytes()).hexdigest()}
    )
    info = SimpleNamespace(st_uid=0, st_mode=stat.S_IFSOCK | 0o600, st_dev=1, st_ino=1)
    monkeypatch.setattr(window, "SOCKET", SimpleNamespace(parent=fake_state, lstat=lambda: info))
    monkeypatch.setattr(window, "nsrun", lambda *args: pytest.fail("no namespace entry for UAPI"))
    return peer, str(binary.resolve()), info


@pytest.mark.parametrize("already_absent", [False, True])
def test_revoke_uses_shared_uapi_and_accepts_repeat(
    revoke_environment, monkeypatch, capsys, already_absent
):
    peer, binary, _ = revoke_environment
    commands = []
    answers = iter(["" if already_absent else peer, "", ""])

    def execute(argv):
        commands.append(argv)
        return next(answers)

    monkeypatch.setattr(window, "run", execute)
    window.revoke()
    assert commands == [
        [binary, "show", window.TUN, "peers"],
        [binary, "set", window.TUN, "peer", peer, "remove"],
        [binary, "show", window.TUN, "peers"],
    ]
    assert json.loads(capsys.readouterr().out) == {
        "peer_removed": True,
        "traffic_cutoff_verified": False,
    }


@pytest.mark.parametrize(
    "kind", ["other_peer", "still_present", "socket_changed", "unsafe_socket", "binary_drift"]
)
def test_revoke_refuses_unowned_or_unverified_state(revoke_environment, monkeypatch, capsys, kind):
    peer, binary, info = revoke_environment
    if kind == "unsafe_socket":
        info.st_mode |= 0o022
    if kind == "binary_drift":
        Path(binary).write_bytes(b"changed synthetic bytes")
    responses = iter(
        ["other" if kind == "other_peer" else peer, "", peer if kind == "still_present" else ""]
    )

    def execute(argv):
        if kind in {"unsafe_socket", "binary_drift"}:
            pytest.fail("must refuse before any UAPI command")
        if kind == "socket_changed" and "set" in argv:
            monkeypatch.setattr(window.SOCKET, "lstat", lambda: SimpleNamespace(st_dev=1, st_ino=2))
        return next(responses)

    monkeypatch.setattr(window, "run", execute)
    with pytest.raises(window.SmokeError):
        window.revoke()
    assert capsys.readouterr().out == ""


def test_cleanup_without_marker_never_deletes_resources(fake_state, monkeypatch):
    monkeypatch.setattr(window.subprocess, "run", lambda *args, **kw: pytest.fail("no ownership"))
    assert window.cleanup()


@pytest.mark.parametrize("failure", [False, True])
def test_partial_setup_cleanup_continues_and_retains_failed_baseline(
    fake_state, monkeypatch, failure
):
    (fake_state / "baseline.json").write_text(
        json.dumps({"schema_version": 2, "hashes": {"synthetic_state": "same"}})
    )
    (fake_state / "server.conf").write_text("synthetic non-secret config")
    (fake_state / "phone.conf").write_text("synthetic non-secret profile")
    (fake_state / "ready.json").write_text('{"endpoint_ready":true}')
    commands = []

    def fake_command(argv, **kwargs):
        commands.append(argv)
        if failure and len(commands) == 1:
            raise subprocess.TimeoutExpired(argv, 2)
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(window.subprocess, "run", fake_command)
    assert window.cleanup() is not failure
    assert len(commands) == len(window.firewall_rules())
    assert commands[0][6] == "PREROUTING"
    assert not (fake_state / "server.conf").exists()
    assert not (fake_state / "phone.conf").exists()
    assert not (fake_state / "ready.json").exists()
    assert (fake_state / "baseline.json").exists() is failure


def test_unit_has_deadline_unconditional_cleanup_no_restart_or_boot_enablement():
    unit = (ROOT / "infra/ansible/files/project-vpn-i011.service").read_text()
    assert "RuntimeMaxSec=600\n" in unit
    assert "KillMode=control-group\n" in unit
    assert (
        "ExecStopPost=/usr/bin/python3 "
        "/opt/project-vpn/acceptance/android_staging_window.py cleanup" in unit
    )
    assert "Restart=no\n" in unit and "[Install]" not in unit
    assert "UMask=0077\n" in unit and "LimitCORE=0\n" in unit


@pytest.mark.parametrize(
    "exception", [window.SmokeError, OSError, ValueError, subprocess.SubprocessError]
)
def test_main_failure_exposes_only_fixed_checkpoint_and_category(monkeypatch, capsys, exception):
    # Synthetic marker text is deliberately not real key/config material.
    marker = "synthetic-sensitive-command-output"
    monkeypatch.setattr(window, "require_environment", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["android_staging_window.py", "preflight"])

    def fail():
        window.checkpoint("container_routes")
        raise exception(marker)

    monkeypatch.setattr(window, "preflight", fail)
    assert window.main() == 1
    output = capsys.readouterr().out
    assert marker not in output
    result = json.loads(output)
    assert result["stage"] == "container_routes"
    assert result["failure_code"] in ("check_rejected", "probe_unavailable")
    assert result["acceptance_passed"] is False


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "/proc/123/ns/net",
        "/run/docker/netns/default",
        "/run/docker/netns/../../etc/passwd",
        "/unapproved/123456abcdef",
        "/run/docker/netns/$(id)",
    ],
)
def test_namespace_path_rejects_proc_unknown_and_traversal_before_io(path):
    with pytest.raises(window.SmokeError, match="invalid_docker_namespace_path"):
        window.docker_namespace_path(path)


@pytest.mark.parametrize("restart,inode_change", [(False, False), (True, False), (False, True)])
def test_docker_route_probe_uses_bind_handle_and_detects_restart(
    monkeypatch, restart, inode_change
):
    before = {
        "id": "a" * 64,
        "pid": 123,
        "running": True,
        "started": "synthetic-start",
        "sandbox": "/var/run/docker/netns/123456abcdef",
    }
    after = {**before, "pid": 124} if restart else before.copy()
    states = iter([before, after])
    monkeypatch.setattr(window, "container_state", lambda name: next(states))

    class FakeHandle:
        def __init__(self):
            self.inodes = iter([11, 12 if inode_change else 11])

        def __str__(self):
            return "/run/docker/netns/123456abcdef"

        def stat(self):
            return SimpleNamespace(st_dev=5, st_ino=next(self.inodes))

    handle = FakeHandle()
    monkeypatch.setattr(window, "docker_namespace_path", lambda path: handle)

    def route_command(argv):
        assert argv[:3] == ["/usr/bin/nsenter", "--net=/run/docker/netns/123456abcdef", "--"]
        assert all("/proc/" not in part for part in argv)
        return '[{"dst":"10.8.1.0/24"}]'

    monkeypatch.setattr(window, "run", route_command)
    if restart or inode_change:
        with pytest.raises(window.SmokeError, match="container_changed_during_probe"):
            window.check_container_routes("amnezia-awg2")
    else:
        window.check_container_routes("amnezia-awg2")


def test_snapshot_ignores_counters_and_route_countdown_but_detects_policy_or_gateway(monkeypatch):
    state = {"count": 1, "expires": 900, "policy": "ACCEPT", "gateway": "fe80::1"}

    def fake_run(argv):
        if argv[0].endswith("docker"):
            return "synthetic 10 true started\nsynthetic 11 true started\n"
        if "save" in argv[0]:
            return f"*filter\n:INPUT {state['policy']} [{state['count']}:123]\nCOMMIT\n"
        if "route" in argv:
            return json.dumps(
                [{"dst": "default", "gateway": state["gateway"], "expires": state["expires"]}]
            )
        return "[]"

    monkeypatch.setattr(window, "run", fake_run)
    first = window.snapshot()
    state.update(count=99, expires=800)
    assert window.snapshot() == first
    state["policy"] = "DROP"
    assert window.snapshot()["filter_nat"] != first["filter_nat"]
    state["gateway"] = "fe80::2"
    assert window.snapshot()["routes6"] != first["routes6"]


@pytest.mark.parametrize(
    "state,expected", [("failed", True), ("inactive", True), ("active", False)]
)
def test_missing_service_cgroup_requires_stopped_service(monkeypatch, state, expected):
    monkeypatch.setattr(
        window, "run", lambda argv: "" if "--property=ControlGroup" in argv else state
    )
    assert window.owned_processes_gone() is expected


@pytest.mark.parametrize("other_process", [False, True])
def test_cleanup_checks_own_cgroup_without_reading_foreign_proc(
    tmp_path, monkeypatch, other_process
):
    group = "/system.slice/" + window.UNIT
    target = tmp_path / group.lstrip("/")
    target.mkdir(parents=True)
    (target / "cgroup.procs").write_text(
        str(window.os.getpid()) + ("\n123456" if other_process else "")
    )
    monkeypatch.setattr(window, "CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(window, "run", lambda argv: group)
    assert window.owned_processes_gone() is not other_process


def test_recovery_refuses_active_service_before_mutations(monkeypatch):
    monkeypatch.setattr(window, "run", lambda argv: "active")
    monkeypatch.setattr(window, "cleanup", lambda: pytest.fail("must not clean active namespace"))
    with pytest.raises(window.SmokeError, match="service_still_running"):
        window.recover()


def test_legacy_recovery_archives_hashes_without_claiming_verified_rollback(
    fake_state, monkeypatch, capsys
):
    original = {
        key: "a" * 64
        for key in (
            "filter_nat",
            "ipv6_filter",
            "routes4",
            "routes6",
            "links",
            "namespaces",
            "containers",
        )
    }
    (fake_state / "baseline.json").write_text(json.dumps(original))
    inputs = fake_state / "input"
    inputs.mkdir()
    monkeypatch.setattr(window, "INPUT", inputs / "public-key")
    monkeypatch.setattr(window, "owned_processes_gone", lambda: True)
    monkeypatch.setattr(window, "cleanup", lambda: False)
    monkeypatch.setattr(
        window,
        "snapshot",
        lambda **kwargs: {**original, "filter_nat": "b" * 64, "routes6": "b" * 64},
    )
    monkeypatch.setattr(
        window, "run", lambda argv: "failed" if "--property=ActiveState" in argv else ""
    )
    assert window.recover()
    assert not (fake_state / "baseline.json").exists()
    assert json.loads((inputs / "recovered-baseline-v1.json").read_text()) == original
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["historical_snapshot_fully_verified"] is False
    assert result["owned_resources_removed"] is True


def test_v2_recovery_keeps_marker_when_stable_state_drift_is_unresolved(fake_state, monkeypatch):
    marker = fake_state / "baseline.json"
    marker.write_text(json.dumps({"schema_version": 2, "hashes": {"synthetic_state": "same"}}))
    monkeypatch.setattr(window, "owned_processes_gone", lambda: True)
    monkeypatch.setattr(window, "run", lambda argv: "failed")
    monkeypatch.setattr(window, "snapshot", lambda **kwargs: {"synthetic_state": "different"})
    monkeypatch.setattr(
        window.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1)
    )
    assert window.recover() is False
    assert marker.exists()

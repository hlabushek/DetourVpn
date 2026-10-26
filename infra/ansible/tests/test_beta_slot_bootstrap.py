"""TEST ONLY schema and fake-runner tests; never configure real host networking."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "node-agent"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

import beta_slot_bootstrap as bootstrap  # noqa: E402

from vpn_api.tunnels.awg_profile import AWG_PARAMETERS  # noqa: E402


def key(seed: int) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode("ascii")


def slot_source() -> bytes:
    return json.dumps(
        {
            "version": 1,
            "slots": [
                {
                    "slot_id": 1,
                    "endpoint": "8.8.8.8:51901",
                    "server_private_key": key(1),
                    "server_public_key": key(2),
                    "header_protection_key": key(3),
                }
            ],
        }
    ).encode()


def test_slot_schema_and_awg_config_are_strict_and_consistent() -> None:
    slots = bootstrap.Slots.model_validate_json(slot_source()).slots
    config = bootstrap.expected_awg_config(slots[0]).decode("ascii")
    assert "PrivateKey = " + key(1) in config
    assert "ListenPort = 51901\n" in config
    assert AWG_PARAMETERS in config
    assert "HeaderProtectionKey = " + key(3) + "\n" in config
    assert key(2) not in config


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint": "192.168.1.5:51901"},
        {"endpoint": "8.8.8.8:51902"},
        {"server_private_key": "invalid"},
        {"server_public_key": "invalid"},
        {"extra": "not-allowed"},
    ],
)
def test_invalid_slot_configuration_fails_closed(changes: dict[str, str]) -> None:
    value = json.loads(slot_source())
    value["slots"][0].update(changes)
    with pytest.raises(bootstrap.ValidationError):
        bootstrap.Slots.model_validate(value)


def test_noncontiguous_slot_configuration_fails_closed() -> None:
    value = json.loads(slot_source())
    value["slots"][0]["slot_id"] = 2
    with pytest.raises(bootstrap.ValidationError):
        bootstrap.Slots.model_validate(value)


def test_slots_reject_distinct_public_endpoint_addresses() -> None:
    value = json.loads(slot_source())
    value["slots"].append(
        {
            "slot_id": 2,
            "endpoint": "9.9.9.9:51902",
            "server_private_key": key(4),
            "server_public_key": key(5),
            "header_protection_key": key(6),
        }
    )
    with pytest.raises(bootstrap.ValidationError):
        bootstrap.Slots.model_validate(value)


def test_only_expected_systemd_ip_launcher_is_treated_as_exec_transition() -> None:
    assert bootstrap._is_systemd_exec_transition("/usr/lib/systemd/systemd-executor", [b"(ip)"])
    assert not bootstrap._is_systemd_exec_transition(
        "/usr/lib/systemd/systemd-executor", [b"(python)"]
    )
    assert not bootstrap._is_systemd_exec_transition("/usr/bin/python3", [b"(ip)"])


def test_readiness_cleanup_allows_only_inactive_core_with_matching_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace_dir = tmp_path / "netns"
    namespace_dir.mkdir()
    namespace = namespace_dir / "detourvpn-beta-slot-1"
    namespace.touch()
    ready_dir = tmp_path / "run" / "slots"
    ready_dir.mkdir(parents=True)
    marker = {
        "slot_id": 1,
        "native_pid": 2_147_483_000,
        "native_netns_inode": namespace.stat().st_ino,
        "server_public_key": key(2),
    }
    monkeypatch.setattr(bootstrap, "NETNS_DIR", namespace_dir)
    monkeypatch.setattr(bootstrap, "READY", ready_dir)
    monkeypatch.setattr(bootstrap, "_private_json", lambda *_args: marker)
    monkeypatch.setattr(bootstrap, "_service_active", lambda _unit: False)

    with pytest.raises(bootstrap.BootstrapError, match="slot_core_identity_unavailable"):
        bootstrap._read_ready(1)
    assert bootstrap._read_ready(1, allow_stopped_core=True).slot_id == 1


def test_unknown_systemd_unit_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=4),
    )
    with pytest.raises(bootstrap.BootstrapError, match="systemd_unit_unavailable"):
        bootstrap._service_active("detourvpn-beta-core@1.service")


def test_host_veth_cleanup_is_fixed_and_conditional(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def existing_veth(arguments: list[str], **_kwargs: object) -> str:
        calls.append(arguments)
        return "dvbh1" if arguments == [bootstrap.IP, "link", "show", "dvbh1"] else ""

    monkeypatch.setattr(bootstrap, "_run", existing_veth)
    bootstrap._remove_host_veth(1)
    assert calls == [
        [bootstrap.IP, "link", "show", "dvbh1"],
        [bootstrap.IP, "link", "delete", "dvbh1"],
    ]


def test_network_rules_are_slot_scoped_and_block_private_destinations() -> None:
    slot = bootstrap.Slots.model_validate_json(slot_source()).slots[0]
    slot_rules = bootstrap._slot_network_rules(slot)
    host_rules = bootstrap._host_firewall_program([slot], "eth0")
    assert 'iifname "dv3s1" oifname "outer0" ip saddr 10.203.71.2 accept' in slot_rules
    assert "10.0.0.0/8" in slot_rules and "192.168.0.0/16" in slot_rules
    assert "8.8.8.8" in slot_rules
    assert 'iifname "outer0" udp dport 51901 accept' in slot_rules
    assert 'iifname "eth0" ip daddr 8.8.8.8 udp dport 51901 dnat to 10.203.82.2:51901' in host_rules
    assert 'iifname "dvbh1" drop' in host_rules
    assert 'oifname "dvbh1" drop' in host_rules
    assert "ct state established,related ct status dnat accept" in host_rules
    assert "ct state established,related accept" in host_rules
    assert 'oifname "eth0" ip saddr 10.203.82.2 masquerade' in host_rules
    assert "flush ruleset" not in host_rules


def test_host_network_preflight_never_enables_global_forwarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = bootstrap.Slots.model_validate_json(slot_source()).slots
    monkeypatch.setattr(bootstrap.Path, "read_text", lambda _path: "0")

    def unexpected_command(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("TEST ONLY: preflight must stop before network commands")

    monkeypatch.setattr(bootstrap, "_run", unexpected_command)
    with pytest.raises(
        bootstrap.BootstrapError, match="host_ipv4_forwarding_must_be_preconfigured"
    ):
        bootstrap._host_network_preflight(slot)


def test_start_sequence_installs_scoped_network_before_veth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST ONLY fake commands validate order; they prove no Linux runtime behavior."""
    slots = bootstrap.Slots.model_validate_json(slot_source()).slots
    netns = tmp_path / "netns"
    netns.mkdir()
    ready_parent = tmp_path / "run" / "detourvpn-node"
    ready_parent.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "NETNS_DIR", netns)
    monkeypatch.setattr(bootstrap, "READY", ready_parent / "slots")
    monkeypatch.setattr(bootstrap, "_preflight_start", lambda _slots: "test-uplink")
    monkeypatch.setattr(bootstrap, "_ensure_uapi_dir", lambda: None)
    namespace = netns / "detourvpn-beta-slot-1"
    calls: list[list[str]] = []
    ready_records: list[tuple[int, int, int]] = []

    def fake_run(arguments: list[str], *, data: str | None = None, required: bool = True) -> str:
        calls.append(arguments)
        if arguments[-1:] == ["public-key"]:
            return key(2)
        if arguments[-1:] == ["listen-port"]:
            return "51901"
        return ""

    class FakeGate:
        def initialize_closed(self) -> bool:
            return False

        def dispose(self) -> None:
            return None

    def fake_gate(_slot_id: int) -> FakeGate:
        return FakeGate()

    def fake_wait(_slot: bootstrap.Slot, inode: int) -> tuple[int, int]:
        return 12345, inode

    original_run = fake_run

    def create_namespace(
        arguments: list[str], *, data: str | None = None, required: bool = True
    ) -> str:
        result = original_run(arguments, data=data, required=required)
        if arguments[:3] == [bootstrap.IP, "netns", "add"]:
            namespace.write_text("TEST ONLY namespace marker", encoding="ascii")
        if arguments[:3] == [bootstrap.IP, "netns", "delete"]:
            namespace.unlink(missing_ok=True)
        return result

    monkeypatch.setattr(bootstrap, "_run", create_namespace)
    monkeypatch.setattr(bootstrap, "_gate", fake_gate)
    monkeypatch.setattr(bootstrap, "_wait_core", fake_wait)
    monkeypatch.setattr(
        bootstrap,
        "_write_ready",
        lambda slot, pid, inode: ready_records.append((slot.slot_id, pid, inode)),
    )
    assert bootstrap.start_slots(slots) == 1
    assert ready_records == [(1, 12345, namespace.stat().st_ino)]
    rendered = [" ".join(call) for call in calls]
    assert any("netns add detourvpn-beta-slot-1" in call for call in rendered)
    assert any("setconf dv3s1" in call for call in rendered)
    host_firewall_index = next(
        index for index, call in enumerate(calls) if call[:3] == [bootstrap.NFT, "-f", "-"]
    )
    veth_index = next(
        index for index, call in enumerate(calls) if call[:3] == [bootstrap.IP, "link", "add"]
    )
    namespace_firewall_index = next(
        index
        for index, call in enumerate(calls)
        if call[:4] == [bootstrap.IP, "netns", "exec", "detourvpn-beta-slot-1"]
        and bootstrap.NFT in call
    )
    assert host_firewall_index < veth_index < namespace_firewall_index
    assert any("net.ipv4.ip_forward=1" in call for call in rendered)
    assert all("/proc/sys/net/ipv4/ip_forward" not in call for call in rendered)


def test_start_failure_stops_cores_and_removes_only_created_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST ONLY fake runner checks rollback command selection; no Linux calls run."""
    slots = bootstrap.Slots.model_validate_json(slot_source()).slots
    netns = tmp_path / "netns"
    netns.mkdir()
    ready_parent = tmp_path / "run" / "detourvpn-node"
    ready_parent.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "NETNS_DIR", netns)
    monkeypatch.setattr(bootstrap, "READY", ready_parent / "slots")
    monkeypatch.setattr(bootstrap, "_preflight_start", lambda _slots: "test-uplink")
    monkeypatch.setattr(bootstrap, "_ensure_uapi_dir", lambda: None)
    namespace = netns / "detourvpn-beta-slot-1"
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], *, data: str | None = None, required: bool = True) -> str:
        calls.append(arguments)
        if arguments == [bootstrap.IP, "link", "show", "dvbh1"]:
            return "dvbh1"
        if arguments[:3] == [bootstrap.IP, "netns", "add"]:
            namespace.write_text("TEST ONLY namespace marker", encoding="ascii")
        if arguments[:3] == [bootstrap.IP, "netns", "delete"]:
            namespace.unlink(missing_ok=True)
        if "setconf" in arguments:
            raise bootstrap.BootstrapError("injected_TEST_ONLY_failure")
        return ""

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    monkeypatch.setattr(bootstrap, "_wait_core", lambda _slot, inode: (12345, inode))
    with pytest.raises(bootstrap.BootstrapError, match="injected_TEST_ONLY_failure"):
        bootstrap.start_slots(slots)
    rendered = [" ".join(call) for call in calls]
    assert any("systemctl stop detourvpn-beta-core@1.service" in call for call in rendered)
    assert any("netns delete detourvpn-beta-slot-1" in call for call in rendered)
    assert any("link delete dvbh1" in call for call in rendered)
    assert any("delete table inet detourvpn_beta" in call for call in rendered)


def test_systemd_units_bind_agent_lifetime_to_bootstrap() -> None:
    agent = (ROOT / "infra/beta-node/detourvpn-beta-node-agent.service").read_text()
    bootstrap_unit = (ROOT / "infra/beta-node/detourvpn-beta-slot-bootstrap.service").read_text()
    core = (ROOT / "infra/beta-node/detourvpn-beta-core@.service").read_text()
    assert "Requires=detourvpn-beta-slot-bootstrap.service" in agent
    assert "BindsTo=detourvpn-beta-slot-bootstrap.service" in agent
    assert "ExecStart=" in bootstrap_unit and "beta_slot_bootstrap.py start" in bootstrap_unit
    assert "ExecStop=" in bootstrap_unit and "beta_slot_bootstrap.py stop" in bootstrap_unit
    assert "RemainAfterExit=yes" in bootstrap_unit
    assert "RuntimeDirectoryPreserve=yes" in bootstrap_unit
    assert (
        "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_NET_ADMIN CAP_KILL CAP_SYS_PTRACE"
        in bootstrap_unit
    )
    assert "DevicePolicy=closed" in bootstrap_unit
    assert "ProtectSystem=strict" not in bootstrap_unit
    assert "PrivateTmp=true" not in bootstrap_unit
    assert "/usr/sbin/ip netns exec detourvpn-beta-slot-%i" in core
    assert "amneziawg-go -f dv3s%i" in core

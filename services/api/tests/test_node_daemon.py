"""Explicit fake native backend tests for durable daemon state; no VPN claims."""

import base64
import hashlib
import importlib.util
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "node_daemon_test_target", ROOT / "services/node-agent/agent.py"
)
assert spec is not None and spec.loader is not None
daemon: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daemon)


class FakeNative:
    """Tests only. Counts fake operations, never calls native programs/network."""

    def __init__(self) -> None:
        self.public_key = base64.b64encode(secrets.token_bytes(32)).decode()
        self.current: list[str] = []
        self.budgets = 0
        self.closes = 0
        self.counter = (11, 19)

    def validate(self) -> None:
        pass

    def initialize_gate(self) -> None:
        self.closes += 1

    def close_gate(self) -> None:
        self.closes += 1

    def peers(self) -> list[str]:
        return list(self.current)

    def counters(self, public: str) -> tuple[int, int]:
        if public not in self.current:
            raise daemon.AgentFault("native_counters_missing")
        return self.counter

    def gate(self, budget: int, seconds: int) -> None:
        assert 1 <= seconds <= 60 and budget == 65536
        self.budgets += 1

    def add(self, public: str) -> None:
        self.current = [public]

    def remove(self, public: str) -> None:
        self.current = [item for item in self.current if item != public]


def lease() -> dict[str, Any]:
    return {
        "command_id": str(uuid4()),
        "peer_id": str(uuid4()),
        "peer_version": 1,
        "operation": "add_peer",
        "public_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "lease_deadline": (datetime.now(UTC) + timedelta(seconds=45)).isoformat(),
        "budget_bytes": 65536,
    }


@pytest.mark.parametrize("fault", [None, "inode", "host", "cgroup", "binary", "owner", "pid"])
def test_namespace_identity_fails_closed_and_pins_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """Synthetic proc metadata only. Real proc/systemd behavior has a separate WSL test."""
    proc = tmp_path / "proc"
    target = proc / "42"
    (target / "ns").mkdir(parents=True)
    (proc / "self/ns").mkdir(parents=True)
    namespace = target / "ns/net"
    namespace.write_bytes(b"synthetic namespace")
    (proc / "self/ns/net").write_bytes(b"synthetic host namespace")
    executable = b"synthetic core, not an executable"
    (target / "exe").write_bytes(executable)
    (target / "cgroup").write_text(
        "0::/system.slice/other.service" if fault == "cgroup" else daemon.ENDPOINT_CGROUP
    )
    inode = namespace.stat().st_ino
    if fault == "inode":
        inode += 1
    if fault == "host":
        inode = (proc / "self/ns/net").stat().st_ino
    ready = tmp_path / "ready.json"
    ready.write_text(
        json.dumps(
            {
                "endpoint_ready": True,
                "native_pid": True if fault == "pid" else 42,
                "native_netns_inode": inode,
            }
        )
    )
    monkeypatch.setattr(daemon, "ENDPOINT_READY", ready)
    monkeypatch.setattr(daemon, "PROC", proc)
    monkeypatch.setattr(
        daemon,
        "CORE_HASH",
        "invalid" if fault == "binary" else hashlib.sha256(executable).hexdigest(),
    )
    monkeypatch.setattr(daemon, "private_path", lambda _: None)
    original_stat = Path.stat

    def fake_stat(path: Path, **kwargs: Any) -> Any:
        if path == target:
            return SimpleNamespace(st_uid=1000 if fault == "owner" else 0)
        return original_stat(path, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    if fault is None:
        fd = daemon.open_endpoint_namespace()
        try:
            assert os.fstat(fd).st_ino == inode
            assert not os.get_inheritable(fd)
        finally:
            os.close(fd)
    else:
        with pytest.raises(daemon.AgentFault, match="endpoint_identity_invalid"):
            daemon.open_endpoint_namespace()


def test_add_retry_does_not_reset_kernel_budget(tmp_path: Path) -> None:
    native = FakeNative()
    agent = daemon.Agent(native, tmp_path / "journal")
    agent.startup()
    body = lease()
    assert agent.execute(body)["applied"] is True
    assert agent.execute(body)["applied"] is True
    assert native.budgets == 1 and native.current == [body["public_key"]]
    assert (
        agent.execute(
            body | {"lease_deadline": (datetime.now(UTC) + timedelta(seconds=59)).isoformat()}
        )["applied"]
        is False
    )
    assert native.budgets == 1


def test_restart_retires_peer_and_never_restores_add(tmp_path: Path) -> None:
    native = FakeNative()
    path = tmp_path / "journal"
    body = lease()
    agent = daemon.Agent(native, path)
    agent.startup()
    agent.execute(body)
    restarted = daemon.Agent(native, path)
    restarted.startup()
    assert native.current == [] and native.budgets == 1
    assert restarted.execute(body)["applied"] is False
    removed = body | {"operation": "remove_peer", "peer_version": 2, "command_id": str(uuid4())}
    ack = restarted.execute(removed)
    assert (ack["rx_bytes"], ack["tx_bytes"], ack["counters_lost"]) == (11, 19, False)
    assert restarted.execute(removed) == ack
    again = daemon.Agent(native, path)
    again.startup()
    assert again.execute(removed) == ack


def test_crash_after_native_add_before_journal_commit_charges_conservatively(
    tmp_path: Path,
) -> None:
    native = FakeNative()
    path = tmp_path / "journal"
    body = lease()
    agent = daemon.Agent(native, path)
    agent.startup()
    agent.execute(body)
    agent.records[body["peer_id"]]["status"] = "intent"
    agent.save()
    restarted = daemon.Agent(native, path)
    restarted.startup()
    assert restarted.execute(body)["applied"] is False
    removed = restarted.execute(body | {"operation": "remove_peer", "peer_version": 2})
    assert removed["counters_lost"] is True and native.current == []


def test_expiry_removes_and_retains_final_counters(tmp_path: Path) -> None:
    native = FakeNative()
    agent = daemon.Agent(native, tmp_path / "journal")
    agent.startup()
    body = lease()
    agent.execute(body)
    agent.records[body["peer_id"]]["deadline"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    agent.expire()
    assert native.current == []
    ack = agent.execute(body | {"operation": "remove_peer", "peer_version": 2})
    assert ack["rx_bytes"] + ack["tx_bytes"] == 30 and not ack["counters_lost"]


@pytest.mark.parametrize(
    "change",
    [
        {"lease_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        {"lease_deadline": (datetime.now(UTC) + timedelta(minutes=4)).isoformat()},
        {"budget_bytes": 1},
    ],
)
def test_invalid_lease_never_installs_peer(tmp_path: Path, change: dict[str, object]) -> None:
    native = FakeNative()
    agent = daemon.Agent(native, tmp_path / "journal")
    agent.startup()
    assert agent.execute(lease() | change)["applied"] is False
    assert native.budgets == 0 and native.current == []


def test_remote_commands_cannot_supply_paths_or_shell(tmp_path: Path) -> None:
    native = FakeNative()
    agent = daemon.Agent(native, tmp_path / "journal")
    agent.startup()
    with pytest.raises(daemon.AgentFault, match="invalid_command"):
        agent.execute(lease() | {"shell": "refused"})

"""ADR-101 pool mode. TEST ONLY simulated awg/nft backend; no kernel, namespace or VPN."""

import base64
import importlib
import sqlite3
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from test_peer_windows import grant
from test_window_runtime import Clock, TestGate, TestPeer

from vpn_api.tunnels.window_journal import RuntimeFault
from vpn_api.tunnels.window_runtime import Gate, Peer, WindowJournal, WindowRuntime


def key() -> str:
    return base64.b64encode(X25519PrivateKey.generate().public_key().public_bytes_raw()).decode()


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "node-agent"))
    return importlib.import_module("window_pool")


class FakeInterface:
    """TEST ONLY: the awg allowed-ips table and nft calls of one pool interface."""

    def __init__(self) -> None:
        self.peers: dict[str, list[str]] = {}
        self.programs: list[str] = []

    def allowed(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.peers.items()}

    def command(self, arguments: list[str]) -> str:
        assert arguments[:3] == ["set", "dv3s1", "peer"]
        if arguments[4] == "remove":
            self.peers.pop(arguments[3])
        else:
            assert arguments[4] == "allowed-ips" and arguments[5].endswith("/32")
            self.peers[arguments[3]] = [arguments[5].removesuffix("/32")]
        return ""

    def nft(self, arguments: list[str], program: str | None = None) -> str:
        self.programs.append(program or " ".join(arguments))
        return ""

    def invalidate_live(self) -> None:
        pass


def test_peer_addresses_are_fixed_and_bounded(pool: ModuleType) -> None:
    assert pool.peer_address(1) == "10.203.72.2"
    assert pool.peer_address(250) == "10.203.72.251"
    for invalid in (0, 251, True, "1"):
        with pytest.raises(pool.PeerFault):
            pool.peer_address(invalid)


def test_many_peers_share_one_interface_without_touching_each_other(pool: ModuleType) -> None:
    interface = FakeInterface()
    first, second = pool.PoolPeer(1, interface), pool.PoolPeer(2, interface)
    a, b = key(), key()
    first.install(a)
    second.install(b)
    first.install(a)  # idempotent replay
    assert interface.peers == {a: ["10.203.72.2"], b: ["10.203.72.3"]}
    with pytest.raises(pool.PeerFault, match="operator_slot_occupied"):
        first.install(key())  # address held by another key
    with pytest.raises(pool.PeerFault, match="pool_peer_key_conflict"):
        pool.PoolPeer(3, interface).install(a)  # same key already bound to index 1
    first.remove()
    assert interface.peers == {b: ["10.203.72.3"]}
    first.remove()  # nothing left to remove: no-op
    assert interface.peers == {b: ["10.203.72.3"]}


def test_gate_program_admits_and_meters_only_its_address(pool: ModuleType) -> None:
    gate = pool.PoolGate(7, FakeInterface())
    program = gate.program(65536, 900_000, replace=True)
    assert program.startswith("delete table inet detour_pool_p7\n")
    assert "elements = { 10.203.72.8 timeout 900000ms }" in program
    for line in (
        'iifname "dv3s1" ip saddr 10.203.72.8 ip saddr @live quota name budget counter',
        'iifname "dv3s1" ip saddr 10.203.72.8 drop',
        'oifname "dv3s1" ip daddr 10.203.72.8 ip daddr @live quota name budget counter',
        'oifname "dv3s1" ip daddr 10.203.72.8 drop',
    ):
        assert line in program
    assert "10.203.72.9" not in program
    with pytest.raises(pool.GateError):
        gate.program(65536, 900_001, replace=False)
    with pytest.raises(pool.GateError):
        gate.program(1024, 1000, replace=False)


def test_gate_liveness_reads_one_shared_snapshot(pool: ModuleType) -> None:
    calls: list[list[str]] = []

    class Snapshot(FakeInterface):
        def nft(self, arguments: list[str], program: str | None = None) -> str:
            calls.append(arguments)
            return ""

    interface: Any = object.__new__(pool.PoolInterface)
    interface._live_at = float("-inf")
    interface._live = set()

    def ruleset(arguments: list[str], program: str | None = None) -> str:
        calls.append(arguments)
        return (
            '{"nftables":[{"set":{"table":"detour_pool_p2","name":"live","elem":["x"]}},'
            '{"set":{"table":"detour_pool_p3","name":"live"}}]}'
        )

    interface.nft = ruleset
    gates = [pool.PoolGate(index, interface) for index in (1, 2, 3)]
    assert [gate.is_live() for gate in gates] == [False, True, False]
    assert len(calls) == 1  # one ruleset read for all peers within the cache window


def test_pool_runtime_runs_peers_independently_with_inner_meter(tmp_path: Path) -> None:
    clock = Clock()
    test_gates = {index: TestGate() for index in (1, 2, 17)}
    test_peers = {index: TestPeer() for index in (1, 2, 17)}
    gates: dict[int, Gate] = dict(test_gates)
    peers: dict[int, Peer] = dict(test_peers)
    journal = WindowJournal(sqlite3.connect(tmp_path / "pool.sqlite"))
    runtime = WindowRuntime(
        uuid4(), journal, gates, peers, now=lambda: clock.now, meter="kernel-inner-ip.v1"
    )
    runtime.startup()
    grants = {
        index: grant(
            node_id=runtime.node_id,
            runtime_id=runtime.runtime_id,
            slot_id=index,
            issued_at=clock.now,
            not_after=clock.now + timedelta(seconds=90),
        )
        for index in (1, 17)
    }
    for value in grants.values():
        assert runtime.apply(value) == "new"
    assert test_peers[1].installs == test_peers[17].installs == 1
    assert test_peers[2].installs == 0
    test_gates[17].amount = 4321
    runtime.revoke(grants[17].connection_id)
    (receipt,) = runtime.journal.pending()
    assert receipt.slot_id == 17 and receipt.meter == "kernel-inner-ip.v1"
    assert receipt.observed_bytes == 4321
    assert test_gates[1].live and runtime.journal.current(1) is not None  # neighbour untouched
    # Slot-mode runtimes still cap at 16 physical slots.
    with pytest.raises(RuntimeFault, match="operator_slots_invalid"):
        WindowRuntime(uuid4(), journal, gates, peers)
    journal.db.close()


def test_pool_runtime_refuses_a_legacy_journal(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE slots (slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 16),"
        " grant_json TEXT NOT NULL, state TEXT NOT NULL);"
    )
    legacy.commit()
    journal = WindowJournal(legacy)
    assert journal.slot_limit() == 16
    test_gates = {index: TestGate() for index in (1, 17)}
    test_peers = {index: TestPeer() for index in (1, 17)}
    gates: dict[int, Gate] = dict(test_gates)
    peers: dict[int, Peer] = dict(test_peers)
    with pytest.raises(RuntimeFault, match="journal_slot_range_legacy"):
        WindowRuntime(uuid4(), journal, gates, peers, meter="kernel-inner-ip.v1")
    legacy.close()

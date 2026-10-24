"""TEST ONLY simulated gate/peer; real SQLite persistence, no AWG/kernel claims."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_peer_windows import grant

from vpn_api.tunnels.v3_contracts import ContractError, WindowGrant
from vpn_api.tunnels.window_journal import RuntimeFault
from vpn_api.tunnels.window_runtime import WindowJournal, WindowRuntime


class TestGate:
    __test__ = False

    def __init__(self) -> None:
        self.live = False
        self.exists = False
        self.amount = 0
        self.opens: list[int] = []
        self.close_error = False
        self.crash_open = False

    def initialize_closed(self) -> bool:
        existed = self.exists
        self.exists = True
        self.close()
        return existed

    def install_closed(self, budget: int) -> None:
        self.live = False
        self.amount = 0

    def open(self, milliseconds: int) -> None:
        self.opens.append(milliseconds)
        self.live = True
        if self.crash_open:
            raise SystemExit("TEST ONLY power loss after kernel open")

    def close(self) -> None:
        if self.close_error:
            raise RuntimeFault("TEST ONLY kernel unavailable")
        self.live = False

    def closed_bytes(self) -> int:
        assert not self.live
        return self.amount

    def is_live(self) -> bool:
        return self.live


class TestPeer:
    __test__ = False

    def __init__(self) -> None:
        self.key: str | None = None
        self.installs = 0

    def install(self, public_key: str) -> None:
        self.key = public_key
        self.installs += 1

    def remove(self) -> None:
        self.key = None


@dataclass
class Clock:
    now: datetime = datetime(2026, 10, 23, 12, tzinfo=UTC)


def setup(path: Path) -> tuple[WindowRuntime, TestGate, TestPeer, Clock]:
    gate, peer, clock = TestGate(), TestPeer(), Clock()
    journal = WindowJournal(sqlite3.connect(path))
    runtime = WindowRuntime(uuid4(), journal, {1: gate}, {1: peer}, now=lambda: clock.now)
    runtime.startup()
    return runtime, gate, peer, clock


def value(runtime: WindowRuntime, clock: Clock) -> WindowGrant:
    return grant(
        node_id=runtime.node_id,
        runtime_id=runtime.runtime_id,
        issued_at=clock.now,
        not_after=clock.now + timedelta(seconds=90),
    )


def next_window(current: WindowGrant, clock: Clock) -> WindowGrant:
    clock.now += timedelta(seconds=30)
    return WindowGrant.model_validate(
        {
            **current.model_dump(),
            "window_id": uuid4(),
            "generation": current.generation + 1,
            "issued_at": clock.now,
            "not_after": clock.now + timedelta(seconds=90),
        }
    )


def test_replay_never_resets_budget_or_deadline(tmp_path: Path) -> None:
    runtime, gate, peer, clock = setup(tmp_path / "journal.db")
    current = value(runtime, clock)
    assert runtime.apply(current) == "new"
    gate.amount = 123
    clock.now += timedelta(seconds=20)
    assert runtime.apply(current) == "replay"
    assert gate.amount == 123 and gate.opens == [84000] and peer.installs == 1
    gate.live = False  # TEST ONLY independent kernel expiry
    with pytest.raises(RuntimeFault, match="window_expired"):
        runtime.apply(current)
    assert len(gate.opens) == 1
    runtime.journal.db.close()


def test_rotation_preserves_peer_and_requires_receipt_ack(tmp_path: Path) -> None:
    runtime, gate, peer, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    gate.amount = 123
    second = next_window(first, clock)
    runtime.apply(second)
    assert peer.installs == 1 and peer.key == first.public_key
    receipt = runtime.journal.pending()[0]
    assert receipt.window_id == first.window_id and receipt.observed_bytes == 123
    third = next_window(second, clock)
    with pytest.raises(RuntimeFault, match="receipt_ack_required"):
        runtime.apply(third)
    runtime.journal.acknowledge(receipt)
    runtime.journal.acknowledge(receipt)
    runtime.apply(third)
    assert len(gate.opens) == 3
    runtime.journal.db.close()


def test_revoke_is_durable_and_old_commands_do_not_reopen(tmp_path: Path) -> None:
    runtime, gate, peer, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    gate.amount = 456
    runtime.revoke(first.connection_id)
    runtime.revoke(first.connection_id)
    assert not gate.live and peer.key is None
    assert runtime.journal.pending()[0].observed_bytes == 456
    with pytest.raises(RuntimeFault, match="connection_fenced"):
        runtime.apply(first)
    runtime.journal.db.close()


@pytest.mark.parametrize("counter_survived", [False, True])
def test_restart_closes_and_captures_original_binding(
    tmp_path: Path,
    counter_survived: bool,
) -> None:
    path = tmp_path / "journal.db"
    runtime, gate, peer, clock = setup(path)
    first = value(runtime, clock)
    runtime.apply(first)
    gate.amount = 789
    gate.exists = counter_survived
    runtime.journal.db.close()
    restarted = WindowRuntime(
        runtime.node_id,
        WindowJournal(sqlite3.connect(path)),
        {1: gate},
        {1: peer},
        now=lambda: clock.now,
    )
    restarted.startup()
    assert not gate.live and peer.key is None and restarted.runtime_id != runtime.runtime_id
    receipt = restarted.journal.pending()[0]
    assert receipt.runtime_id == runtime.runtime_id
    assert receipt.observed_bytes == (789 if counter_survived else None)
    with pytest.raises(RuntimeFault, match="connection_fenced"):
        restarted.apply(first)
    with pytest.raises(RuntimeFault, match="receipt_ack_required"):
        restarted.apply(value(restarted, clock))
    restarted.journal.acknowledge(receipt)
    restarted.apply(value(restarted, clock))
    restarted.journal.db.close()


def test_crash_after_open_before_commit_retains_unknown(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    runtime, gate, peer, clock = setup(path)
    first = value(runtime, clock)
    gate.crash_open = True
    with pytest.raises(SystemExit, match="TEST ONLY"):
        runtime.apply(first)
    assert gate.live
    runtime.journal.db.close()
    restarted = WindowRuntime(
        runtime.node_id,
        WindowJournal(sqlite3.connect(path)),
        {1: gate},
        {1: peer},
        now=lambda: clock.now,
    )
    restarted.startup()
    assert not gate.live and restarted.journal.pending()[0].observation == "unknown"
    assert restarted.journal.pending()[0].observed_bytes is None
    restarted.journal.db.close()


def test_gate_close_failure_has_no_false_final_receipt(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    gate.close_error = True
    with pytest.raises(RuntimeFault, match="TEST ONLY"):
        runtime.revoke(first.connection_id)
    assert runtime.journal.fenced(first.connection_id)
    assert runtime.journal.pending() == []
    gate.close_error = False
    runtime.revoke(first.connection_id)
    assert len(runtime.journal.pending()) == 1
    runtime.journal.db.close()


@pytest.mark.parametrize("field", ["runtime_id", "node_id", "generation"])
def test_binding_and_late_renewal_fail_closed(tmp_path: Path, field: str) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    forged = WindowGrant.model_validate(
        {**first.model_dump(), field: 2 if field == "generation" else uuid4()}
    )
    with pytest.raises(ContractError):
        runtime.apply(forged)
    assert not gate.live
    runtime.apply(first)
    clock.now = first.not_after - timedelta(seconds=35)
    second = next_window(first, clock)
    with pytest.raises(ContractError, match="renewal_too_late"):
        runtime.apply(second)
    assert len(gate.opens) == 1
    runtime.journal.db.close()


def test_delayed_revoke_of_settled_generation_closes_current(tmp_path: Path) -> None:
    runtime, gate, peer, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    second = next_window(first, clock)
    runtime.apply(second)
    assert gate.live and runtime.journal.receipt(first.window_id) is not None
    runtime.close_grant(first)
    assert not gate.live and peer.key is None
    assert runtime.journal.receipt(second.window_id) is not None
    with pytest.raises(RuntimeFault, match="connection_fenced"):
        runtime.apply(second)
    runtime.journal.db.close()


def test_kernel_expiry_blocks_new_generation_before_absolute_deadline(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    second = next_window(first, clock)
    gate.live = False  # TEST ONLY budget exhausted, despite valid wall-clock deadline.
    with pytest.raises(RuntimeFault, match="window_expired"):
        runtime.apply(second)
    runtime.close_grant(second)
    assert len(gate.opens) == 1
    receipts = runtime.journal.pending()
    assert {r.window_id for r in receipts} == {first.window_id, second.window_id}
    final = runtime.journal.receipt(second.window_id)
    assert final is not None and final.observation == "unknown"
    runtime.journal.db.close()


def test_recovery_only_before_admission_and_never_guesses_bytes(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    old = value(runtime, clock).model_copy(update={"runtime_id": uuid4()})
    recovered = runtime.recover(old)
    assert recovered.observation == "unknown" and recovered.observed_bytes is None
    assert runtime.recover(old) == recovered and not gate.live
    runtime.journal.acknowledge(recovered)
    runtime.apply(value(runtime, clock))
    with pytest.raises(RuntimeFault, match="recovery_phase_conflict"):
        runtime.recover(old)
    assert gate.live
    runtime.journal.db.close()


@pytest.mark.parametrize("counter", [-1, 2**63])
def test_invalid_counter_stays_unknown(tmp_path: Path, counter: int) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    gate.amount = counter
    runtime.revoke(first.connection_id)
    receipt = runtime.journal.receipt(first.window_id)
    assert receipt is not None and receipt.observation == "unknown"
    assert receipt.observed_bytes is None and not gate.live
    runtime.journal.db.close()

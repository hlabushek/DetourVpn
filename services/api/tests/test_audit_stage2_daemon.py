"""TEST ONLY audit stage 2 (H1 / ADR-169, M1 / ADR-170): node daemon error handling.

Simulated gate/peer/control only; no root, kernel, nftables, AWG or network access.
"""

import contextlib
import importlib
import io
import json
import sqlite3
import urllib.error
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from test_window_daemon import SyntheticControl, control, daemon
from test_window_runtime import TestGate, TestPeer, setup, value

from vpn_api.tunnels.v3_contracts import WindowCommand, WindowCommands
from vpn_api.tunnels.window_journal import RuntimeFault

__all__ = ["daemon"]


def gate_error(daemon: ModuleType) -> type[Exception]:
    error: type[Exception] = importlib.import_module("window_gate").GateError
    return error


def test_native_gate_failure_in_tick_is_a_control_fault_not_a_crash(
    daemon: ModuleType, tmp_path: Path
) -> None:
    """H1: main() survives only RuntimeFault; anything else closes every gate on the node."""
    runtime, gate, _, clock = setup(tmp_path / "state.sqlite")
    error = gate_error(daemon)

    def failing_install(_budget: int) -> None:
        raise error("kernel_gate_operation_failed")  # TEST ONLY simulated nft failure

    gate.install_closed = failing_install  # type: ignore[method-assign, assignment]
    response = WindowCommands(
        runtime_id=runtime.runtime_id,
        ready=True,
        commands=[WindowCommand(operation="window", grant=value(runtime, clock))],
        acknowledged_receipts=[],
    ).model_dump(mode="json")
    try:
        daemon.tick(runtime, SyntheticControl([response]))
    except RuntimeFault:
        pass  # handled by the main loop: one node keeps serving its other users
    except Exception as escaped:  # noqa: BLE001 - the assertion is the point of the test
        pytest.fail(f"{type(escaped).__name__} escapes tick(); main() would close every gate")
    finally:
        runtime.journal.db.close()


def refused(daemon: ModuleType, body: bytes, status: int = 409) -> str:
    transport = control(daemon)

    class Refusing:
        def open(self, request: Any, *, timeout: int) -> io.BytesIO:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "TEST ONLY",
                None,  # type: ignore[arg-type]
                io.BytesIO(body),
            )

    transport.opener = Refusing()
    with pytest.raises(RuntimeFault) as fault:
        transport.post("poll", {})
    return str(fault.value)


def test_server_runtime_mismatch_is_distinguishable_from_transport_loss(
    daemon: ModuleType,
) -> None:
    """M1 (ADR-170): a 409 window_runtime_mismatch is not reported as a lost network."""
    body = json.dumps({"error": {"code": "window_runtime_mismatch"}}).encode()
    assert refused(daemon, body) == daemon.RUNTIME_MISMATCH == "rejected_window_runtime_mismatch"


@pytest.mark.parametrize(
    "body",
    [
        b"<html>bad gateway</html>",
        json.dumps({"error": {"code": "Upper-Case 1"}}).encode(),
        json.dumps({"error": {"code": "x" * 65}}).encode(),
        json.dumps({"error": {"code": "a" * 5000}}).encode(),  # over the 4 KiB body cap
        b"",
    ],
    ids=["html", "not-a-fixed-code", "too-long-code", "oversized-body", "empty"],
)
def test_server_rejection_codes_are_fixed_and_bounded(daemon: ModuleType, body: bytes) -> None:
    """M1 (ADR-170): only [a-z_]{1,64} codes from at most 4 KiB of body are reported."""
    assert refused(daemon, body, 502) == "control_rejected"


class FakeSlotGate(TestGate):
    __test__ = False

    def __init__(self, *_args: object) -> None:
        super().__init__()

    def dispose(self) -> None:
        pass


class FakeSlotPeer(TestPeer):
    __test__ = False

    def __init__(self, *_args: object) -> None:
        super().__init__()

    def dispose(self) -> None:
        pass


@dataclass
class MainRun:
    code: int
    loops: int
    registrations: int
    gate: FakeSlotGate


def run_main(
    daemon: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tick: Callable[..., None],
    stop_after: int = 5,
) -> MainRun:
    """TEST ONLY: main() with simulated files, namespace, gate, peer and journal."""
    files = {
        str(daemon.CONFIG): json.dumps(
            {"node_id": str(uuid4()), "api_origin": "https://example.invalid", "slot_count": 1}
        ).encode(),
        str(daemon.CREDENTIAL): b"x" * 43,  # TEST ONLY synthetic credential
        str(daemon.READY / "1.json"): json.dumps(
            {
                "slot_id": 1,
                "native_pid": 4242,
                "native_netns_inode": 7,
                "server_public_key": "A" * 43 + "=",
            }
        ).encode(),
    }
    monkeypatch.setattr(daemon, "private_read", lambda path, _limit: files[str(path)])
    monkeypatch.setattr(
        daemon,
        "os",
        SimpleNamespace(
            geteuid=lambda: 0,
            O_RDONLY=0,
            open=lambda *_args: 99,
            fstat=lambda _fd: SimpleNamespace(st_ino=7),
            close=lambda _fd: None,
        ),
    )
    gates: list[FakeSlotGate] = []

    def make_gate(*args: object) -> FakeSlotGate:
        gates.append(FakeSlotGate(*args))
        return gates[-1]

    monkeypatch.setattr(daemon, "SlotGate", make_gate)
    monkeypatch.setattr(daemon, "SlotPeer", FakeSlotPeer)

    @contextlib.contextmanager
    def journal(_path: Path) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(tmp_path / "state.sqlite")
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(daemon, "open_private_journal", journal)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    registrations: list[int] = []
    monkeypatch.setattr(daemon, "register", lambda *_args: registrations.append(1))
    monkeypatch.setattr(daemon, "tick", tick)
    loops: list[float] = []

    def sleep(seconds: float) -> None:
        loops.append(seconds)
        if len(loops) >= stop_after:
            setattr(daemon, "STOP", True)  # noqa: B010 - module global

    monkeypatch.setattr(daemon.time, "sleep", sleep)
    monkeypatch.setattr(daemon, "STOP", False)
    code = daemon.main()
    return MainRun(code, len(loops), len(registrations), gates[0])


def test_main_loop_survives_a_native_error_and_keeps_gates(
    daemon: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H1 (ADR-169): a non-identity native error is retried; the process does not exit."""
    error = gate_error(daemon)

    def failing(*_args: object) -> None:
        raise error("kernel_gate_operation_failed")

    run = run_main(daemon, tmp_path, monkeypatch, failing)
    assert run.code == 0 and run.loops == 5  # stopped only by the simulated SIGTERM


def test_main_loop_exits_after_repeated_core_identity_failure(
    daemon: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H1 (ADR-169): a core or namespace that is gone, seen repeatedly, ends the process."""
    peer_fault = importlib.import_module("window_peer").PeerFault

    def gone(*_args: object) -> None:
        raise peer_fault("operator_core_identity_invalid")

    run = run_main(daemon, tmp_path, monkeypatch, gone, stop_after=50)
    assert run.code == 1 and run.loops == daemon.IDENTITY_FAILURE_LIMIT - 1


def test_malformed_poll_response_is_a_control_fault(daemon: ModuleType, tmp_path: Path) -> None:
    """H1 (ADR-169): a response that is not the contract never escapes as a crash."""
    runtime, _, _, _ = setup(tmp_path / "state.sqlite")
    try:
        with pytest.raises(RuntimeFault, match="control_response_invalid"):
            daemon.tick(runtime, SyntheticControl([{"runtime_id": "not-a-uuid"}]))
        transport = control(daemon)

        class Html:
            def open(self, _request: Any, *, timeout: int) -> io.BytesIO:
                return io.BytesIO(b"<html>bad gateway</html>")

        transport.opener = Html()
        with pytest.raises(RuntimeFault, match="control_response_invalid"):
            transport.post("poll", {})
    finally:
        runtime.journal.db.close()


def test_main_loop_registers_again_after_the_server_forgets_its_runtime(
    daemon: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: after a database restore the server answers window_runtime_mismatch forever."""

    def mismatch(*_args: object) -> None:
        # What Control.post raises for HTTP 409 window_runtime_mismatch (see the test above).
        raise RuntimeFault(daemon.RUNTIME_MISMATCH)

    run = run_main(daemon, tmp_path, monkeypatch, mismatch)
    assert run.code == 0 and run.loops == 5
    assert run.registrations == 5, "the agent registers again after every mismatch"


def test_other_control_faults_do_not_register_again(
    daemon: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lost(*_args: object) -> None:
        raise RuntimeFault("control_transport_unavailable")

    run = run_main(daemon, tmp_path, monkeypatch, lost)
    assert run.code == 0 and run.registrations == 1

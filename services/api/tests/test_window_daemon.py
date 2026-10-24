"""TEST ONLY in-process HTTP/control transport; no remote node or VPN acceptance."""

import importlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_window_runtime import next_window, setup, value

from vpn_api.tunnels.v3_contracts import WindowCommand, WindowCommands
from vpn_api.tunnels.window_journal import RuntimeFault


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "node-agent"))
    return importlib.import_module("window_daemon")


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        "https://user:password@example.invalid",
        "https://example.invalid:444",
        "https://example.invalid/api",
        "https://example.invalid?token=example",
        "https://example.invalid#fragment",
        "https:///missing-host",
    ],
)
def test_origin_rejects_credential_or_destination_ambiguity(
    daemon: ModuleType, origin: str
) -> None:
    with pytest.raises(ValidationError):
        daemon.Configuration(node_id=uuid4(), api_origin=origin, slot_count=1)


class Reply:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, *, timeout: int) -> io.BytesIO:
        assert timeout == 5
        self.calls.append(request)
        if isinstance(self.payload, Exception):
            raise self.payload
        raw = self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()
        return io.BytesIO(raw)


def control(daemon: ModuleType) -> Any:
    return daemon.Control(
        daemon.Configuration(node_id=uuid4(), api_origin="https://example.invalid/", slot_count=1),
        "x" * 43,  # TEST ONLY synthetic node credential.
    )


def test_transport_fixed_paths_nonce_and_no_redirect(daemon: ModuleType) -> None:
    transport = control(daemon)
    assert any(isinstance(h, daemon.NoRedirect) for h in transport.opener.handlers)
    reply = Reply({"runtime_id": None})
    transport.opener = reply
    for _ in range(2):
        assert transport.post("inventory", {}) == {"runtime_id": None}
    assert all(
        item.full_url == "https://example.invalid/api/internal/v3/windows/inventory"
        and item.method == "POST"
        for item in reply.calls
    )
    assert reply.calls[0].get_header("X-agent-nonce") != reply.calls[1].get_header("X-agent-nonce")
    with pytest.raises(RuntimeFault, match="control_operation_forbidden"):
        transport.post("../external", {})
    assert len(reply.calls) == 2
    assert (
        daemon.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")
        is None
    )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"x" * 131073, "control_response_oversized"),
        ([], "control_response_invalid"),
        (urllib.error.URLError("TEST ONLY"), "control_transport_unavailable"),
    ],
    ids=["oversized", "not-an-object", "transport-error"],
)
def test_transport_bounds_and_failure_codes(
    daemon: ModuleType, payload: object, error: str
) -> None:
    transport = control(daemon)
    transport.opener = Reply(payload)
    with pytest.raises(RuntimeFault, match=error):
        transport.post("poll", {})


class SyntheticControl:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, operation: str, payload: dict[str, object]) -> dict[str, Any]:
        self.calls.append((operation, payload))
        return self.responses.pop(0)


def test_register_lost_start_ack_does_not_create_another_epoch(
    daemon: ModuleType, tmp_path: Path
) -> None:
    runtime, _, _, _ = setup(tmp_path / "state.sqlite")
    transport = SyntheticControl([{"runtime_id": str(runtime.runtime_id)}])
    daemon.register(runtime, transport, [])
    assert [operation for operation, _ in transport.calls] == ["inventory"]
    runtime.journal.db.close()


def test_tick_rotation_receipt_ack_and_delayed_revoke(daemon: ModuleType, tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "state.sqlite")
    first = value(runtime, clock)

    def command(operation: str, grant: object) -> dict[str, Any]:
        return WindowCommands.model_validate(
            {
                "runtime_id": runtime.runtime_id,
                "ready": True,
                "commands": [{"operation": operation, "grant": grant}],
                "acknowledged_receipts": [],
            }
        ).model_dump(mode="json")

    transport = SyntheticControl([command("window", first)])
    daemon.tick(runtime, transport)
    gate.amount = 321
    second = next_window(first, clock)
    transport.responses.append(command("window", second))
    daemon.tick(runtime, transport)
    receipt = runtime.journal.pending()[0]
    transport.responses.append(
        WindowCommands(
            runtime_id=runtime.runtime_id,
            ready=True,
            commands=[WindowCommand(operation="revoke", grant=first)],
            acknowledged_receipts=[receipt],
        ).model_dump(mode="json")
    )
    daemon.tick(runtime, transport)
    assert receipt.observed_bytes == 321 and not gate.live
    assert transport.calls[-1][1]["active_windows"] == [str(second.window_id)]
    assert [r.window_id for r in runtime.journal.pending()] == [second.window_id]
    runtime.journal.db.close()


@pytest.mark.parametrize("wrong_runtime", [False, True])
def test_tick_rejects_wrong_epoch_or_admission_phase(
    daemon: ModuleType, tmp_path: Path, wrong_runtime: bool
) -> None:
    runtime, gate, _, clock = setup(tmp_path / "state.sqlite")
    response = WindowCommands(
        runtime_id=uuid4() if wrong_runtime else runtime.runtime_id,
        ready=False,
        commands=[WindowCommand(operation="window", grant=value(runtime, clock))],
        acknowledged_receipts=[],
    )
    with pytest.raises(RuntimeFault, match="response_runtime_conflict|unexpected_admission_phase"):
        daemon.tick(runtime, SyntheticControl([response.model_dump(mode="json")]))
    assert not gate.live
    runtime.journal.db.close()

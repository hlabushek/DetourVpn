"""In-place pool conversion tool. TEST ONLY scripted hosts; no SSH, node or API is touched."""

from typing import Any
from uuid import UUID

import pytest
from scripts import connect_beta_node as connect
from scripts import convert_beta_node_pool as convert
from test_connect_beta_node import GERMANY_ID, germany

PROGRAMS = {
    **connect.PROGRAMS,
    "API_POOL_STATUS": convert.API_POOL_STATUS,
    "API_DRAIN": convert.API_DRAIN,
    "API_SET_POOL": convert.API_SET_POOL,
    "NODE_CONVERT": convert.NODE_CONVERT,
    "NODE_RESTORE": convert.NODE_RESTORE,
    "NODE_POOL_READY": convert.NODE_POOL_READY,
}


class ScriptedHosts(connect.Hosts):
    def __init__(self, status: dict[str, str], fail: str | None = None) -> None:
        super().__init__(germany())
        self.status, self.fail = status, fail
        self.calls: list[str] = []
        self.names = {id(value): name for name, value in PROGRAMS.items()}

    def _answer(self, program: str, args: tuple[str, ...]) -> bytes:
        name = self.names[id(program)]
        self.calls.append(name)
        if self.fail == name:
            raise connect.ConnectError(f"connect_remote_step_failed:9:{name}")
        api_keys = (
            "row",
            "open_peers",
            "drained",
            "in_manifest",
            "pool_peers",
            "api_supports_pool",
        )
        answers: dict[str, str] = {
            "NODE_READ_AGENT_ID": f"node_id={GERMANY_ID}",
            "API_POOL_STATUS": "\n".join(f"{k}={self.status[k]}" for k in api_keys),
            "NODE_POOL_READY": "\n".join(
                f"{k}={self.status[k]}"
                for k in ("agent_code_pool", "node_pool_peers", "node_slots")
            ),
            "API_DRAIN": "drain_requested=0",
            "API_SET_STATUS": "node_row_" + (args[3] if len(args) > 3 else "x"),
            "NODE_STOP": "beta_node_agent_stopped",
            "NODE_CONVERT": "node_converted_to_pool",
            "API_SET_POOL": "manifest_pool_set backup=x",
            "NODE_START": "beta_node_agent_active",
            "NODE_ENABLE_BOOT": "beta_node_agent_enabled_at_boot",
            "API_WAIT_RUNTIME": "beta_node_authenticated_inventory_and_poll",
            "API_RESTORE_FILES": "pre_add_files_restored",
            "NODE_RESTORE": "node_restored",
        }
        return (answers[name] + "\n").encode()

    def api(
        self, program: str, args: tuple[str, ...] = (), payload: Any = None, label: str = ""
    ) -> bytes:
        return self._answer(program, args)

    def node_run(
        self, program: str, args: tuple[str, ...] = (), payload: Any = None, label: str = ""
    ) -> bytes:
        return self._answer(program, args)


def ready(**changes: str) -> dict[str, str]:
    return {
        "row": "active",
        "open_peers": "0",
        "drained": "1",
        "in_manifest": "1",
        "pool_peers": "0",
        "api_supports_pool": "1",
        "agent_code_pool": "1",
        "node_pool_peers": "0",
        "node_slots": "1",
        **changes,
    }


@pytest.mark.parametrize(
    "name",
    [
        "API_POOL_STATUS",
        "API_DRAIN",
        "API_SET_POOL",
        "NODE_CONVERT",
        "NODE_RESTORE",
        "NODE_POOL_READY",
    ],
)
def test_remote_programs_compile(name: str) -> None:
    compile(PROGRAMS[name], name, "exec")


def test_dry_run_reads_only_and_refuses_old_code() -> None:
    hosts = ScriptedHosts(ready())
    assert convert.convert(hosts, 32, False)["converted"] is False
    assert set(hosts.calls) == {"NODE_READ_AGENT_ID", "API_POOL_STATUS", "NODE_POOL_READY"}
    for change, code in (
        ({"agent_code_pool": "0"}, "convert_agent_code_not_updated"),
        ({"api_supports_pool": "0"}, "convert_api_not_updated"),
        ({"node_slots": "2"}, "convert_node_shape_unsupported"),
    ):
        with pytest.raises(convert.ConvertError, match=code):
            convert.convert(ScriptedHosts(ready(**change)), 32, False)


def test_execute_drains_disables_converts_and_restarts_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.convert_beta_node_pool.time.sleep", lambda _s: None)
    hosts = ScriptedHosts(ready())
    assert convert.convert(hosts, 32, True)["converted"] is True
    ordered = [
        c
        for c in hosts.calls
        if c not in {"API_POOL_STATUS", "NODE_POOL_READY", "NODE_READ_AGENT_ID"}
    ]
    assert ordered == [
        "API_DRAIN",
        "API_SET_STATUS",  # disabled
        "NODE_STOP",
        "NODE_CONVERT",
        "API_SET_POOL",
        "API_SET_STATUS",  # active
        "NODE_START",
        "NODE_ENABLE_BOOT",
        "API_WAIT_RUNTIME",
    ]


def test_failure_after_node_change_restores_node_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.convert_beta_node_pool.time.sleep", lambda _s: None)
    hosts = ScriptedHosts(ready(), fail="API_SET_POOL")
    with pytest.raises(convert.ConvertError, match="convert_failed_rolled_back"):
        convert.convert(hosts, 32, True)
    tail = hosts.calls[hosts.calls.index("API_SET_POOL") + 1 :]
    assert tail == ["API_RESTORE_FILES", "NODE_RESTORE", "API_SET_STATUS", "NODE_START"]


def test_drain_times_out_when_connections_stay_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.convert_beta_node_pool.time.sleep", lambda _s: None)
    hosts = ScriptedHosts(ready(open_peers="2", drained="0"))
    with pytest.raises(convert.ConvertError, match="convert_drain_timeout"):
        convert.drain(hosts, UUID(GERMANY_ID), wait_s=0)
    assert "NODE_STOP" not in hosts.calls  # nothing on the node was touched

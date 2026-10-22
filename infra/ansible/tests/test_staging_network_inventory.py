"""Local tests with explicitly fake command results; no Docker/SSH/network calls."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "files" / "staging_network_inventory.py"
SPEC = importlib.util.spec_from_file_location("staging_inventory", SOURCE)
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


class FakeCommands:
    """Synthetic network metadata only; never invokes a subprocess."""

    def __init__(self):
        self.calls = []
        self.reads = {}
        self.change = None
        self.invalid_pid = None
        self.running = True
        self.network_ids = "c" * 64

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == [inventory.DOCKER, "container", "inspect"]:
            name = argv[-1]
            self.reads[name] = self.reads.get(name, 0) + 1
            value = {
                "id": "a" * 64,
                "pid": self.invalid_pid if self.invalid_pid is not None else 1234,
                "running": self.running,
                "started_at": "fake-start-time-1",
                "networks": {
                    "fake-bridge": {
                        "IPAddress": "192.0.2.10",
                        "IPPrefixLen": 24,
                        "Aliases": ["unselected_metadata"],
                    }
                },
                "Env": ["unselected_metadata"],
            }
            if self.reads[name] > 1 and self.change:
                value[self.change] = {
                    "id": "b" * 64,
                    "pid": 2345,
                    "started_at": "fake-start-time-2",
                    "networks": {},
                }[self.change]
            return json.dumps(value)
        if argv[:3] == [inventory.DOCKER, "network", "ls"]:
            return self.network_ids
        if argv[:3] == [inventory.DOCKER, "network", "inspect"]:
            return json.dumps(
                {
                    "name": "fake-bridge",
                    "driver": "bridge",
                    "ipam": [{"Subnet": "192.0.2.0/24", "Options": "unselected_metadata"}],
                    "Labels": "unselected_metadata",
                }
            )
        if "route" in argv:
            return json.dumps(
                [{"dst": "198.51.100.0/24", "dev": "fake0", "unselected": "unselected_metadata"}]
            )
        if "rule" in argv:
            return json.dumps([{"priority": 0, "src": "all", "table": "local"}])
        raise AssertionError(f"Unexpected command in test: {argv}")


def test_collect_reads_only_expected_tools_and_selects_fields():
    fake = FakeCommands()
    report = inventory.collect(fake)
    encoded = json.dumps(report)
    assert "unselected_metadata" not in encoded
    assert "198.51.100.0/24" in encoded
    assert "192.0.2.0/24" in encoded
    assert [row["name"] for row in report["containers"]] == list(inventory.CONTAINERS)
    for command in fake.calls:
        assert command[0] in (inventory.IP, inventory.DOCKER, inventory.NSENTER)
        if command[0] == inventory.NSENTER:
            assert command[1:4] == ["--net=/proc/1234/ns/net", "--", inventory.IP]
        if command[0] == inventory.DOCKER:
            assert command[2] in ("inspect", "ls")
            assert "--format" in command or command[1:3] == ["network", "ls"]
    assert len([cmd for cmd in fake.calls if cmd[0] == inventory.NSENTER]) == 8


@pytest.mark.parametrize("pid", [0, 1, -3, "1234", True])
def test_invalid_pid_rejected_before_namespace_entry(pid):
    fake = FakeCommands()
    fake.invalid_pid = pid
    with pytest.raises(inventory.InventoryError):
        inventory.collect(fake)
    assert not any(cmd[0] == inventory.NSENTER for cmd in fake.calls)


def test_stopped_container_fails_closed():
    fake = FakeCommands()
    fake.running = False
    with pytest.raises(inventory.InventoryError):
        inventory.collect(fake)
    assert not any(cmd[0] == inventory.NSENTER for cmd in fake.calls)


@pytest.mark.parametrize("change", ["id", "pid", "started_at", "networks"])
def test_restart_or_network_change_rejects_snapshot(change):
    fake = FakeCommands()
    fake.change = change
    with pytest.raises(inventory.InventoryError, match="container_changed_during_probe"):
        inventory.collect(fake)


@pytest.mark.parametrize("ids", ["--untrusted-option", "", "c" * 63])
def test_invalid_network_id_rejected(ids):
    fake = FakeCommands()
    fake.network_ids = ids
    with pytest.raises(inventory.InventoryError):
        inventory.collect(fake)
    assert not any(cmd[1:3] == ["network", "inspect"] for cmd in fake.calls)


def test_command_failure_never_includes_raw_output(monkeypatch):
    monkeypatch.setattr(
        inventory.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="unselected_metadata", stderr="unselected_metadata"
        ),
    )
    with pytest.raises(inventory.InventoryError, match="^probe_command_failed$"):
        inventory.run_command([inventory.IP, "-j", "-4", "route", "show"])


def test_main_failure_emits_no_partial_inventory(monkeypatch, capsys):
    monkeypatch.setattr(inventory.os, "geteuid", lambda: 0, raising=False)

    def fail():
        raise ValueError("unselected_metadata")

    monkeypatch.setattr(inventory, "collect", fail)
    assert inventory.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "inventory_incomplete_do_not_choose_subnet"}


def test_main_requires_privilege_without_probing(monkeypatch, capsys):
    monkeypatch.setattr(inventory.os, "geteuid", lambda: 1000, raising=False)
    assert inventory.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "requires_staging_sudo"}

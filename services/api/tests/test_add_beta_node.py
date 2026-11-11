# ruff: noqa: E501
"""Add-node wizard (ADR-094 stage 3). TEST ONLY: no host is contacted, no key is real."""

import argparse
import json
from pathlib import Path

import pytest
from scripts import add_beta_node as wizard
from scripts.beta_nodes import BetaNode, InventoryError


def namespace(**changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "name": "france",
        "host": "9.9.9.9",
        "country": "fr",
        "city": "Paris",
        "inventory": Path("unused"),
    }
    values.update(changes)
    return argparse.Namespace(**values)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir()
    return tmp_path


def fake_keygen(monkeypatch: pytest.MonkeyPatch) -> None:
    def ensure(name: str) -> Path:
        key = wizard.ssh_directory() / name
        key.write_text("private")
        key.with_name(key.name + ".pub").write_text("ssh-ed25519 AAAAFAKE " + name + "\n")
        return key

    monkeypatch.setattr(wizard, "ensure_key", ensure)


def test_prepare_adds_a_valid_entry_and_prints_one_paste_line(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_keygen(monkeypatch)
    inventory = home / "nodes.json"
    assert wizard.prepare(namespace(inventory=inventory)) == 0
    saved = json.loads(inventory.read_text(encoding="utf-8"))["nodes"][0]
    assert saved["name"] == "detourvpn-beta-france" and saved["country_code"] == "FR"
    assert saved["udp_ports"] == [51901, 51916]
    out = capsys.readouterr().out
    assert "ssh-ed25519 AAAAFAKE detourvpn-beta-france" in out
    assert "ip_forward=1" in out and "Ubuntu 24.04" in out and "PRIVATE" not in out
    assert "private" not in out.replace("privateKey", "")


def test_prepare_refuses_duplicates_and_bad_input(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_keygen(monkeypatch)
    inventory = home / "nodes.json"
    wizard.prepare(namespace(inventory=inventory))
    with pytest.raises(wizard.WizardError, match="wizard_node_already_in_inventory"):
        wizard.prepare(namespace(inventory=inventory))
    before = inventory.read_bytes()
    with pytest.raises(InventoryError):
        wizard.prepare(namespace(inventory=inventory, name="spain", host="10.0.0.5"))
    with pytest.raises(wizard.WizardError, match="wizard_name_invalid"):
        wizard.prepare(namespace(inventory=inventory, name="Bad Name"))
    assert inventory.read_bytes() == before  # a rejected entry never touches the file


def test_run_executes_every_stage_in_order_and_saves_progress(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    def stage(key: str, code: int = 0) -> object:
        def run(node: BetaNode, inventory: Path) -> int:
            calls.append(key)
            return code

        return run

    monkeypatch.setattr(wizard, "load_inventory", lambda path: {"detourvpn-beta-france": _node()})
    monkeypatch.setattr(
        wizard,
        "stages",
        lambda *_: [
            (k, k, stage(k, 1 if k == "bind" and not calls.count("fixed") else 0))
            for k in ("preflight", "install", "register", "bind", "activate")
        ],
    )
    args = argparse.Namespace(node="detourvpn-beta-france", inventory=Path("x"))
    assert wizard.run(args) == 1
    assert calls == ["preflight", "install", "register", "bind"]
    assert wizard.load_done("detourvpn-beta-france") == {"install", "register"}
    calls.append("fixed")
    monkeypatch.setattr(
        wizard,
        "stages",
        lambda *_: [
            (k, k, stage(k)) for k in ("preflight", "install", "register", "bind", "activate")
        ],
    )
    calls.clear()
    assert wizard.run(args) == 0
    assert calls == ["preflight", "bind", "activate"]  # finished stages are skipped
    assert "не приёмка" in capsys.readouterr().out


def _node() -> BetaNode:
    from scripts.beta_nodes import parse_inventory

    raw = json.dumps(
        {
            "version": 1,
            "nodes": [
                {
                    "name": "detourvpn-beta-france",
                    "region": "france",
                    "country_code": "FR",
                    "city": "Paris",
                    "host": "9.9.9.9",
                    "ssh_user": "root",
                    "ssh_key": "detourvpn-beta-france",
                    "ssh_port": 22,
                    "udp_ports": [51901, 51916],
                }
            ],
        }
    ).encode()
    return parse_inventory(raw)["detourvpn-beta-france"]


def test_run_refuses_helsinki_and_unknown_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard, "load_inventory", lambda path: {})
    with pytest.raises(wizard.WizardError, match="wizard_node_not_allowed"):
        wizard.run(argparse.Namespace(node="detourvpn-beta-helsinki", inventory=Path("x")))


def test_stage_list_is_the_reviewed_chain() -> None:
    assert [key for key, _title, _run in wizard.stages()] == [
        "preflight",
        "install",
        "register",
        "bind",
        "activate",
    ]


def test_install_stage_passes_the_pool_size(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(wizard.install_beta_node, "main", lambda argv: seen.append(argv) or 0)
    install = dict((key, run) for key, _title, run in wizard.stages(32))["install"]
    plain = dict((key, run) for key, _title, run in wizard.stages())["install"]

    class Node:
        name = "detourvpn-beta-france"

    install(Node(), Path("x"))  # type: ignore[arg-type]
    plain(Node(), Path("x"))  # type: ignore[arg-type]
    assert seen[0][-3:] == ["--pool-peers", "32", "--execute"]
    assert "--pool-peers" not in seen[1]

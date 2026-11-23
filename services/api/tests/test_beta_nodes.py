"""Beta node inventory and candidate preflight (ADR-094). TEST ONLY: no host is contacted."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts import check_candidate_node as check
from scripts.beta_nodes import InventoryError, parse_inventory

ROOT = Path(__file__).resolve().parents[3]


def node(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "detourvpn-beta-frankfurt",
        "region": "frankfurt",
        "country_code": "DE",
        "city": "Frankfurt",
        "host": "198.51.100.20",
        "ssh_user": "root",
        "ssh_key": "detourvpn-beta-test",
        "ssh_port": 22,
        "udp_ports": [51820, 51829],
    }
    value.update(changes)
    return value


def document(*nodes: dict[str, Any], **changes: Any) -> bytes:
    return json.dumps({"version": 1, "nodes": list(nodes), **changes}).encode()


def parse(*nodes: dict[str, Any], **changes: Any) -> Any:
    return parse_inventory(document(*nodes, **changes), allow_non_global=True)


def test_committed_example_is_valid_and_uses_documentation_addresses() -> None:
    data = (ROOT / "infra" / "beta" / "nodes.example.json").read_bytes()
    nodes = parse_inventory(data, allow_non_global=True)
    assert list(nodes) == ["detourvpn-beta-helsinki"]
    with pytest.raises(InventoryError, match="inventory_host_must_be_public_ipv4"):
        parse_inventory(data)  # strict mode rejects documentation addresses


def test_valid_node_resolves_key_inside_the_ssh_directory() -> None:
    result = parse(node())["detourvpn-beta-frankfurt"]
    assert (result.country_code, result.udp_ports, result.ssh_key.name) == (
        "DE",
        (51820, 51829),
        "detourvpn-beta-test",
    )


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"name": "helsinki"}, "inventory_value_invalid"),
        ({"name": "detourvpn-beta-"}, "inventory_value_invalid"),
        ({"region": "Frankfurt"}, "inventory_value_invalid"),
        ({"country_code": "de"}, "inventory_value_invalid"),
        ({"ssh_user": "Root User"}, "inventory_value_invalid"),
        ({"ssh_key": "../id_rsa"}, "inventory_value_invalid"),
        ({"ssh_key": "id_rsa"}, "inventory_value_invalid"),
        ({"host": "example.com"}, "inventory_host_invalid"),
        ({"host": "2001:db8::1"}, "inventory_host_must_be_public_ipv4"),
        ({"ssh_port": 0}, "inventory_ssh_port_invalid"),
        ({"ssh_port": True}, "inventory_ssh_port_invalid"),
        ({"udp_ports": [51820]}, "inventory_udp_ports_invalid"),
        ({"udp_ports": [51829, 51820]}, "inventory_udp_ports_invalid"),
        ({"udp_ports": [51820, 51900]}, "inventory_udp_ports_invalid"),
        ({"udp_ports": [80, 90]}, "inventory_udp_ports_invalid"),
        ({"city": ""}, "inventory_city_invalid"),
    ],
)
def test_invalid_values_fail_with_fixed_codes(changes: dict[str, Any], code: str) -> None:
    with pytest.raises(InventoryError, match=code):
        parse(node(**changes))


def test_unknown_missing_and_extra_keys_are_rejected() -> None:
    extra = node(unexpected="x")
    missing = node()
    del missing["city"]
    for bad in (extra, missing):
        with pytest.raises(InventoryError, match="inventory_node_keys_invalid"):
            parse(bad)
    with pytest.raises(InventoryError, match="inventory_shape_invalid"):
        parse_inventory(document(node(), extra_top="x"), allow_non_global=True)
    with pytest.raises(InventoryError, match="inventory_version_invalid"):
        parse_inventory(
            json.dumps({"version": 2, "nodes": [node()]}).encode(), allow_non_global=True
        )


def test_duplicates_count_limits_and_oversize_are_rejected() -> None:
    other = {"name": "detourvpn-beta-other", "region": "other", "host": "198.51.100.30"}
    for field in ("name", "region", "host"):
        # the second node reuses exactly one field of the first and differs in the others
        second = node(**{k: v for k, v in other.items() if k != field})
        with pytest.raises(InventoryError, match=f"inventory_duplicate_{field}"):
            parse(node(), second)
    sixteen = [
        node(name=f"detourvpn-beta-n{i}", region=f"r{i}", host=f"198.51.100.{i + 1}")
        for i in range(16)
    ]
    assert len(parse(*sixteen)) == 16
    with pytest.raises(InventoryError, match="inventory_node_count_invalid"):
        parse(*sixteen, node(name="detourvpn-beta-n16", region="r16", host="198.51.100.99"))
    with pytest.raises(InventoryError, match="inventory_node_count_invalid"):
        parse()
    with pytest.raises(InventoryError, match="inventory_too_large"):
        parse_inventory(b" " * 70000)
    with pytest.raises(InventoryError, match="inventory_not_json"):
        parse_inventory(b"{nope")


GOOD = {
    "os_id": "ubuntu", "os_version": "24.04", "arch": "x86_64", "uid": "0", "cpus": "2",
    "mem_available_kb": "1500000", "root_free_kb": "20000000", "ip_forward": "1",
    "default_routes": "1", "host_ip_assigned": "1", "systemd": "1", "systemd_executor": "1",
    "udp_ports_busy": "0", "ntp_synchronized": "yes", "clock_offset_ms": "12",
}  # fmt: skip


def verdict(values: dict[str, str]) -> dict[str, bool]:
    return {str(c["check"]): bool(c["ok"]) for c in check.evaluate(values)}


def test_ready_host_passes_every_check() -> None:
    assert all(verdict(GOOD).values())


@pytest.mark.parametrize(
    ("key", "value", "failing"),
    [
        ("uid", "1000", "ssh_root"),
        ("os_id", "centos", "os_supported"),
        ("os_version", "18.04", "os_supported"),
        ("os_version", "22.04", "os_supported"),
        ("systemd_executor", "0", "systemd_executor"),
        ("arch", "aarch64", "arch_x86_64"),
        ("systemd", "0", "systemd"),
        ("default_routes", "2", "single_default_route"),
        ("default_routes", "0", "single_default_route"),
        ("host_ip_assigned", "0", "public_ipv4_assigned_directly"),
        ("ip_forward", "0", "ip_forward_enabled"),
        ("udp_ports_busy", "1", "udp_ports_free"),
        ("mem_available_kb", "100000", "memory_advisory"),
        ("root_free_kb", "100", "disk_advisory"),
    ],
)
def test_each_failure_is_reported_under_its_own_check(key: str, value: str, failing: str) -> None:
    result = verdict({**GOOD, key: value})
    assert result[failing] is False
    assert [name for name, ok in result.items() if not ok] == [failing]


def test_missing_facts_fail_closed() -> None:
    result = verdict({})
    assert not any(result.values())


def test_parse_output_ignores_noise_and_oversized_values() -> None:
    values = check.parse_output("uid=0\nnot a pair\nbad key=1\nlong=" + "x" * 100 + "\ncpus=2\n")
    assert values == {"uid": "0", "cpus": "2"}


class FakeResult:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout.encode()
        self.returncode = returncode


def write_inventory(tmp_path: Path) -> Path:
    path = tmp_path / "nodes.json"
    path.write_bytes(document(node(host="8.8.8.8")))  # a real-looking public address
    return path


def run_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, facts: dict[str, str], *extra: str
) -> tuple[int, str]:
    text = "".join(f"{k}={v}\n" for k, v in facts.items())
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> FakeResult:
        calls.append(command)
        assert kwargs["input"] == check.REMOTE_PROGRAM.encode("ascii")
        return FakeResult(text)

    monkeypatch.setattr(subprocess, "run", fake_run)
    inventory = write_inventory(tmp_path)
    code = check.main(["--node", "detourvpn-beta-frankfurt", "--inventory", str(inventory), *extra])
    command = calls[0]
    assert "StrictHostKeyChecking=yes" in command and "BatchMode=yes" in command
    # ssh joins its arguments into one remote command line: nothing after the host may carry a
    # shell metacharacter (a "|" in the port list once became a pipe).
    assert not any(c in "".join(command[command.index("bash") :]) for c in "|;&$`<> ")
    assert command[-4:-2] == ["bash", "-s"] or "bash" in command
    return code, " ".join(command)


def test_cli_prints_a_verdict_without_the_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, command = run_cli(monkeypatch, tmp_path, GOOD)
    out = capsys.readouterr().out
    assert code == 0 and '"verdict": "ready"' in out
    assert "8.8.8.8" not in out and "root@8.8.8.8" in command  # only argv carries it


def test_cli_exit_code_and_receipt_for_a_not_ready_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = tmp_path / "receipt.json"
    code, _ = run_cli(monkeypatch, tmp_path, {**GOOD, "ip_forward": "0"}, "--receipt", str(receipt))
    assert code == 1
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["verdict"] == "not_ready" and "8.8.8.8" not in receipt.read_text()
    assert "8.8.8.8" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):  # a receipt is never overwritten
        check.main(
            [
                "--node", "detourvpn-beta-frankfurt",
                "--inventory", str(write_inventory(tmp_path)),
                "--receipt", str(receipt),
            ]
        )  # fmt: skip


def test_cli_errors_use_fixed_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = write_inventory(tmp_path)
    assert check.main(["--node", "detourvpn-beta-nope", "--inventory", str(inventory)]) == 2
    assert "candidate_not_in_inventory" in capsys.readouterr().err
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult("", 255))
    assert check.main(["--node", "detourvpn-beta-frankfurt", "--inventory", str(inventory)]) == 2
    err = capsys.readouterr().err
    assert "candidate_ssh_failed" in err and "8.8.8.8" not in err
    assert check.main(["--node", "x", "--inventory", str(tmp_path / "missing.json")]) == 2
    assert "inventory_file_unreadable" in capsys.readouterr().err

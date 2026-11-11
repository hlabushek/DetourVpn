"""Beta node installer (ADR-094 stage 2a). TEST ONLY: no host is contacted, no key is real."""

# ruff: noqa: E501, S607

import base64
import io
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
from scripts import install_beta_node as install
from scripts.beta_nodes import BetaNode, parse_inventory

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = json.dumps(
    {
        "version": 1,
        "nodes": [
            {
                "name": "detourvpn-beta-frankfurt",
                "region": "frankfurt",
                "country_code": "DE",
                "city": "Frankfurt",
                "host": "9.9.9.9",
                "ssh_user": "root",
                "ssh_key": "detourvpn-beta-test",
                "ssh_port": 22,
                "udp_ports": [51901, 51916],
            }
        ],
    }
).encode()


def make_node() -> BetaNode:
    return parse_inventory(INVENTORY, allow_non_global=True)["detourvpn-beta-frankfurt"]


def package_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "package"
    (directory / "bin").mkdir(parents=True)
    (directory / "bin" / "awg").write_bytes(b"binary")
    (directory / "window_daemon.py").write_bytes(b"code")
    (directory / "SHA256SUMS").write_bytes(b"x  window_daemon.py\n")
    return directory


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required for a syntax check")
def test_remote_program_is_valid_shell() -> None:
    result = subprocess.run(
        ["bash", "-n"], input=install.REMOTE_PROGRAM.encode(), capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_remote_program_guards_do_not_use_and_lists() -> None:
    """In a ``cmd1 && cmd2`` list set -e ignores a failing cmd1, which would skip a guard."""
    for line in install.REMOTE_PROGRAM.splitlines():
        if line.startswith(("test ", "  test ")):
            assert "&&" not in line, line


def test_remote_program_refuses_existing_state_and_only_removes_exact_paths() -> None:
    program = install.REMOTE_PROGRAM
    assert 'test ! -e "$path"' in program and 'test ! -L "$path"' in program
    for line in program.splitlines():
        if "rm -rf" in line:
            assert line.strip() in {
                "trap 'rm -rf -- \"$stage\"' EXIT",
                "rm -rf -- /opt/detourvpn-node /etc/detourvpn-node /run/detourvpn-node",
            }
    assert "node-agent.service >/dev/null" not in program  # the agent is never started
    assert "systemctl enable --now detourvpn-beta-slot-bootstrap.service" in program
    assert "start detourvpn-beta-node-agent" not in program
    assert (
        'test "$(systemctl is-active detourvpn-beta-node-agent.service || true)" = inactive'
        in program
    )


def test_pins_match_the_working_node() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", install.UV_SHA256)
    assert re.fullmatch(r"[0-9a-f]{64}", install.PYTHON_SHA256)
    assert install.UV_URL.startswith("https://github.com/astral-sh/uv/releases/download/0.11.17/")
    assert install.EXPECTED_FREEZE.count("==") == 5
    requirements = (ROOT / "services" / "node-agent" / "window-requirements.txt").read_text()
    for pin in install.EXPECTED_FREEZE.split():
        assert pin in requirements, pin


def test_slot_source_renders_with_fresh_secrets_and_the_fixed_ports() -> None:
    first = install.generate_slot_source(make_node(), 2)
    second = install.generate_slot_source(make_node(), 2)
    assert first != second
    rendered = install.render_configs(first)
    slots = json.loads(rendered["node_slots"])["slots"]
    assert [slot["endpoint"] for slot in slots] == ["9.9.9.9:51901", "9.9.9.9:51902"]
    assert sorted(rendered["awg_configs"]) == ["slot-1.conf", "slot-2.conf"]
    assert len({slot["server_public_key"] for slot in slots}) == 2


@pytest.mark.parametrize("slots", [0, 17, -1])
def test_slot_count_is_bounded(slots: int) -> None:
    with pytest.raises(install.InstallError, match="install_slot_count_invalid"):
        install.generate_slot_source(make_node(), slots)


def test_archive_has_fixed_names_modes_and_owner(tmp_path: Path) -> None:
    data = install.build_archive(
        package_directory(tmp_path), {"slots.json": b"{}", "awg/slot-1.conf": b"c"}
    )
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = {member.name: member for member in archive.getmembers()}
    assert set(members) == {
        "pkg/SHA256SUMS",
        "pkg/bin/awg",
        "pkg/window_daemon.py",
        "cfg/slots.json",
        "cfg/awg/slot-1.conf",
    }
    assert members["pkg/bin/awg"].mode == 0o700
    assert all(m.mode == 0o600 for name, m in members.items() if name != "pkg/bin/awg")
    assert all((m.uid, m.gid, m.uname, m.gname) == (0, 0, "root", "root") for m in members.values())


def test_archive_refuses_symlinks(tmp_path: Path) -> None:
    directory = package_directory(tmp_path)
    try:
        (directory / "link").symlink_to(directory / "window_daemon.py")
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(install.InstallError, match="install_package_symlink"):
        install.build_archive(directory, {})


def test_parse_steps_keeps_only_marker_lines() -> None:
    raw = b"step=checks\nnoise 9.9.9.9 secret\nstep=files\nfailed_step=venv\nstep=BAD LINE\n"
    assert install.parse_steps(raw) == ["step=checks", "step=files", "failed_step=venv"]


class Recorder:
    def __init__(self, program: subprocess.CompletedProcess[bytes], upload_code: int = 0) -> None:
        self.calls: list[tuple[list[str], bytes]] = []
        self.program = program
        self.upload_code = upload_code

    def __call__(
        self, node: BetaNode, remote: list[str], stdin: bytes, timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((remote, stdin))
        if remote and remote[0].startswith("umask 077"):
            return subprocess.CompletedProcess([], self.upload_code, b"", b"")
        if remote and remote[0].startswith("rm -rf --"):
            return subprocess.CompletedProcess([], 0, b"", b"")
        return self.program


def completed(code: int, out: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], code, out.encode(), b"")


def test_execute_uploads_then_runs_the_fixed_program(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(completed(0, "step=checks\nstep=files\nstep=done\n"))
    monkeypatch.setattr(install, "_ssh", recorder)
    steps = install.execute(make_node(), b"tar-bytes", 1)
    assert steps[-1] == "step=done"
    upload, program = recorder.calls
    assert upload[1] == b"tar-bytes"
    assert re.fullmatch(
        r"umask 077 && mkdir /root/detourvpn-node-install-[0-9a-f]{32} && tar -x -C /root/detourvpn-node-install-[0-9a-f]{32} --no-same-owner",
        upload[0][0],
    )
    assert program[0][:3] == ["bash", "-s", "--"]
    assert program[1] == install.REMOTE_PROGRAM.encode()
    assert re.fullmatch(r"[0-9a-f]{32}", program[0][3])


def test_execute_reports_only_the_failed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install,
        "_ssh",
        Recorder(completed(1, "step=checks\nsecret 9.9.9.9\nfailed_step=venv\n")),
    )
    with pytest.raises(install.InstallError) as error:
        install.execute(make_node(), b"x", 1)
    assert str(error.value) == "install_failed:venv"


def test_execute_without_the_done_marker_is_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, "_ssh", Recorder(completed(0, "step=checks\n")))
    with pytest.raises(install.InstallError, match="install_unverified"):
        install.execute(make_node(), b"x", 1)


def test_failed_upload_removes_the_staging_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(completed(0, ""), upload_code=1)
    monkeypatch.setattr(install, "_ssh", recorder)
    with pytest.raises(install.InstallError, match="install_upload_failed"):
        install.execute(make_node(), b"x", 1)
    assert recorder.calls[-1][0][0].startswith("rm -rf -- /root/detourvpn-node-install-")
    assert len(recorder.calls) == 2  # the fixed program never ran


def run_cli(
    monkeypatch: pytest.MonkeyPatch, ready: bool, *extra: str
) -> tuple[int, Recorder, list[str]]:
    recorder = Recorder(completed(0, "step=done\n"))
    built: list[str] = []
    monkeypatch.setattr(
        install, "load_inventory", lambda path: parse_inventory(INVENTORY, allow_non_global=True)
    )
    monkeypatch.setattr(install, "run_remote", lambda node: {})
    monkeypatch.setattr(
        install, "evaluate", lambda values: [{"check": "x", "ok": ready, "required": True}]
    )

    def fake_prepare(
        node: BetaNode, slots: int, artifacts: Path, pool_peers: int | None = None
    ) -> tuple[bytes, int]:
        built.append("payload")
        return b"archive", 18

    monkeypatch.setattr(install, "prepare_payload", fake_prepare)
    monkeypatch.setattr(install, "_ssh", recorder)
    code = install.main(["--node", "detourvpn-beta-frankfurt", *extra])
    return code, recorder, built


def test_dry_run_never_writes_to_the_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, recorder, built = run_cli(monkeypatch, True)
    out = capsys.readouterr().out
    assert code == 0 and recorder.calls == [] and built == ["payload"]
    report: dict[str, Any] = json.loads(out)
    assert report["mode"] == "dry_run" and "installed" not in report
    assert "9.9.9.9" not in out


def test_not_ready_candidate_stops_before_any_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, recorder, built = run_cli(monkeypatch, False, "--execute")
    assert code == 1 and recorder.calls == [] and built == []
    assert json.loads(capsys.readouterr().err) == {
        "installed": False,
        "error": "install_candidate_not_ready",
    }


def test_execute_flag_installs_and_writes_a_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = tmp_path / "receipt.json"
    code, recorder, _ = run_cli(monkeypatch, True, "--execute", "--receipt", str(receipt))
    assert code == 0 and len(recorder.calls) == 2
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["installed"] is True and saved["steps"] == ["step=done"]
    assert "9.9.9.9" not in receipt.read_text(encoding="utf-8")
    assert "9.9.9.9" not in capsys.readouterr().out


def test_unknown_node_is_rejected_with_a_fixed_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(install, "load_inventory", lambda path: {})
    assert install.main(["--node", "detourvpn-beta-missing"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "install_node_not_in_inventory"


def test_failure_message_carries_fixed_diagnostics_only(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        "step=bootstrap",
        "diag=beta_awg_port_in_use",
        "diag=result_exit_code",
        "diag=9.9.9.9 leak",
        "failed_step=bootstrap",
    ]
    output = chr(10).join(lines) + chr(10)
    monkeypatch.setattr(install, "_ssh", Recorder(completed(1, output)))
    with pytest.raises(install.InstallError) as error:
        install.execute(make_node(), b"x", 1)
    assert str(error.value) == "install_failed:bootstrap:beta_awg_port_in_use,result_exit_code"


def test_pool_install_source_renders_pool_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-101: --pool-peers reaches the agent config, node slots file and API manifest."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "node-agent"))
    from beta_slot_config import render_configs

    from vpn_api.tunnels.window_profiles import WindowManifest

    pool = render_configs(install.generate_slot_source(make_node(), 1, 40))
    assert json.loads(pool["agent_config"])["pool_peers"] == 40
    assert json.loads(pool["node_slots"])["pool_peers"] == 40
    manifest = WindowManifest.model_validate_json(pool["api_manifest"])
    assert manifest.nodes[0].pool_peers == 40 and len(manifest.nodes[0].peer_slots) == 40
    plain = render_configs(install.generate_slot_source(make_node(), 2))
    for name in ("agent_config", "node_slots", "api_manifest"):
        assert b"pool_peers" not in plain[name]
    for slots, peers in ((2, 40), (1, 1), (1, 251)):
        with pytest.raises(install.InstallError, match="install_pool_invalid"):
            install.generate_slot_source(make_node(), slots, peers)


def test_pool_bootstrap_rules_cover_the_pool_subnet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "node-agent"))
    import beta_slot_bootstrap as bootstrap

    source = json.loads(install.generate_slot_source(make_node(), 1, 8))
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    private = base64.b64decode(source["slots"][0]["server_private_key"])
    public = X25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw()
    slot = bootstrap.Slot.model_validate(
        {**source["slots"][0], "server_public_key": base64.b64encode(public).decode()}
    )
    import window_gate

    # Pool mode deletes the per-port gate by this name; it must be the gate's real table.
    assert bootstrap.GATE_TABLE == window_gate.TABLE
    pooled = bootstrap._slot_network_rules(slot, pool=True)
    assert "ip saddr 10.203.72.0/24 accept" in pooled
    assert "ip saddr 10.203.72.0/24 masquerade" in pooled
    assert "10.203.71.2" not in pooled
    single = bootstrap._slot_network_rules(slot)
    assert "ip saddr 10.203.71.2 accept" in single and "10.203.72" not in single
    with pytest.raises(ValueError):
        bootstrap.Slots.model_validate(
            {"version": 1, "slots": [slot.model_dump(), slot.model_dump()], "pool_peers": 8}
        )

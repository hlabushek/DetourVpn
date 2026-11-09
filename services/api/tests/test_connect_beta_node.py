"""Connecting a beta node to the control plane (ADR-094 stage 2b). TEST ONLY: no host is contacted."""

# ruff: noqa: E501, S102

import base64
import json
import re
import shlex
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from scripts import connect_beta_node as connect
from scripts.beta_nodes import BetaNode, parse_inventory

ROOT = Path(__file__).resolve().parents[3]
HELSINKI_ID = "1d230f49-ed36-45f2-91ea-86fe212346f8"
GERMANY_ID = "7a1f2c34-0b5d-4e6f-8a9b-1c2d3e4f5a6b"
CREDENTIAL = "A" * 43
KEY_A = base64.b64encode(bytes([1]) * 32).decode()
KEY_B = base64.b64encode(bytes([2]) * 32).decode()
KEY_C = base64.b64encode(bytes([3]) * 32).decode()
HEADER_A = base64.b64encode(bytes([4]) * 32).decode()
HEADER_B = base64.b64encode(bytes([5]) * 32).decode()

INVENTORY = json.dumps(
    {
        "version": 1,
        "nodes": [
            {
                "name": "detourvpn-beta-germany",
                "region": "germany",
                "country_code": "DE",
                "city": "Frankfurt",
                "host": "9.9.9.9",
                "ssh_user": "root",
                "ssh_key": "detourvpn-beta-test",
                "ssh_port": 22,
                "udp_ports": [51901, 51916],
            },
            {
                "name": "detourvpn-beta-helsinki",
                "region": "helsinki",
                "country_code": "FI",
                "city": "Helsinki",
                "host": "1.1.1.1",
                "ssh_user": "root",
                "ssh_key": "detourvpn-beta-test",
                "ssh_port": 22,
                "udp_ports": [51901, 51916],
            },
        ],
    }
).encode()


def germany() -> BetaNode:
    return parse_inventory(INVENTORY, allow_non_global=True)["detourvpn-beta-germany"]


def helsinki_entry() -> dict[str, Any]:
    return {
        "node_id": HELSINKI_ID,
        "slots": [
            {
                "slot_id": 1,
                "endpoint": "1.1.1.1:51901",
                "server_public_key": KEY_A,
                "header_protection_key": HEADER_A,
            }
        ],
    }


def germany_entry(node_id: str = GERMANY_ID) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "slots": [
            {
                "slot_id": 1,
                "endpoint": "9.9.9.9:51901",
                "server_public_key": KEY_B,
                "header_protection_key": HEADER_B,
            }
        ],
    }


def manifest_bytes(*entries: dict[str, Any]) -> bytes:
    return connect.canonical_json({"version": 3, "nodes": list(entries)})


@pytest.mark.parametrize("name", sorted(connect.PROGRAMS))
def test_every_remote_program_compiles(name: str) -> None:
    compile(connect.PROGRAMS[name], name, "exec")


def test_database_programs_verify_the_beta_target() -> None:
    for name in (
        "API_STATUS",
        "API_READ_CREDENTIAL",
        "API_MERGE_MANIFEST",
        "API_ACTIVATE_PREFLIGHT",
        "API_SET_STATUS",
        "API_WAIT_RUNTIME",
    ):
        assert "load_settings()" in connect.PROGRAMS[name], name
    prologue = connect.API_PROLOGUE
    for requirement in (
        'u.database == "detourvpn_beta"',
        'u.username == "vpn_api"',
        "u.port == 25433",
        "s.enable_window_tunnels",
        "s.beta_invites_only",
    ):
        assert requirement in prologue


def test_remote_programs_never_print_secrets() -> None:
    forbidden = re.compile(
        r"(print|write)\([^)]*(saved\[|\bcredential\)|private_key|header_protection_key|raw_entry|\braw\))"
    )
    for name, source in connect.PROGRAMS.items():
        if name in {"API_READ_CREDENTIAL", "NODE_READ_SLOTS"}:
            continue  # these hand secrets back to the local caller only
        assert not forbidden.search(source), name


def test_provision_key_is_deterministic_per_node() -> None:
    assert connect.provision_key(germany()) == connect.provision_key(germany())
    other = parse_inventory(INVENTORY, allow_non_global=True)["detourvpn-beta-helsinki"]
    assert connect.provision_key(germany()) != connect.provision_key(other)


def test_merge_manifest_adds_a_node_and_keeps_the_existing_one_byte_identical() -> None:
    old = manifest_bytes(helsinki_entry())
    merged = json.loads(connect.merge_manifest(old, germany_entry()))
    assert merged["version"] == 3
    assert merged["nodes"][0] == helsinki_entry()
    assert merged["nodes"][1] == germany_entry()


def test_merge_manifest_is_idempotent_and_refuses_a_conflicting_entry() -> None:
    once = connect.merge_manifest(manifest_bytes(helsinki_entry()), germany_entry())
    assert connect.merge_manifest(once, germany_entry()) == once
    changed = germany_entry()
    changed["slots"][0]["server_public_key"] = KEY_C
    with pytest.raises(ValueError, match="manifest_conflict"):
        connect.merge_manifest(once, changed)


def test_merge_manifest_rejects_duplicate_identity() -> None:
    clash = germany_entry("0b1f2c34-0b5d-4e6f-8a9b-1c2d3e4f5a6b")
    clash["slots"][0]["server_public_key"] = KEY_A  # same public key as Helsinki
    with pytest.raises(ValueError):
        connect.merge_manifest(manifest_bytes(helsinki_entry()), clash)


@pytest.mark.parametrize("raw", [b"{}", b'{"version": 2, "nodes": []}', b"not json"])
def test_merge_manifest_rejects_a_malformed_existing_file(raw: bytes) -> None:
    with pytest.raises(ValueError):
        connect.merge_manifest(raw, germany_entry())


def test_embedded_functions_are_the_tested_functions() -> None:
    namespace: dict[str, Any] = {
        "json": json,
        "WindowManifest": connect.WindowManifest,
        "ipaddress": __import__("ipaddress"),
    }
    exec(
        connect._embed(connect.canonical_json, connect.merge_manifest, connect.render_caddyfile),
        namespace,
    )  # noqa: S102 - same source as the tested functions.
    merged = namespace["merge_manifest"](manifest_bytes(helsinki_entry()), germany_entry())
    assert merged == connect.merge_manifest(manifest_bytes(helsinki_entry()), germany_entry())
    assert (
        namespace["render_caddyfile"]("\tremote_ip 1.1.1.1\n", "9.9.9.9")
        == "\tremote_ip 1.1.1.1 9.9.9.9\n"
    )


def test_caddyfile_allowlist_is_extended_once_on_the_real_template() -> None:
    template = (ROOT / "infra" / "beta" / "Caddyfile.ip").read_text(encoding="utf-8")
    installed = template.replace("{$DETOURVPN_BETA_NODE_IP}", "1.1.1.1")
    updated = connect.render_caddyfile(installed, "9.9.9.9")
    assert updated.count("remote_ip 1.1.1.1 9.9.9.9") == 1
    assert connect.render_caddyfile(updated, "9.9.9.9") == updated
    assert updated.replace(" 9.9.9.9", "") == installed


@pytest.mark.parametrize(
    "text",
    [
        "no matcher here\n",
        "remote_ip 1.1.1.1\nremote_ip 2.2.2.2\n",
        "remote_ip {$DETOURVPN_BETA_NODE_IP}\n",
        "remote_ip\n",
    ],
)
def test_caddyfile_edit_fails_closed(text: str) -> None:
    with pytest.raises(ValueError):
        connect.render_caddyfile(text, "9.9.9.9")


def test_caddyfile_edit_rejects_a_bad_address() -> None:
    with pytest.raises(ValueError):
        connect.render_caddyfile("remote_ip 1.1.1.1\n", "not-an-ip; reverse_proxy")


def test_python_command_round_trips_the_program_and_quotes_arguments() -> None:
    command = connect.python_command("/usr/bin/python3", "print('x')", ("a b", "$(id)"))
    parts = shlex.split(command)
    assert parts[0] == "/usr/bin/python3" and parts[1] == "-c"
    encoded = re.search(r"b64decode\('([^']+)'\)", parts[2])
    assert encoded is not None
    assert base64.b64decode(encoded.group(1)) == b"print('x')"
    assert parts[3:] == ["a b", "$(id)"]


def test_manifest_entry_requires_the_fixed_port_and_host() -> None:
    slots = [
        {
            "slot_id": 1,
            "endpoint": "9.9.9.9:51901",
            "server_public_key": KEY_B,
            "header_protection_key": HEADER_B,
            "extra": "dropped",
        }
    ]
    entry = connect.manifest_entry(UUID(GERMANY_ID), slots, "9.9.9.9")
    assert entry == germany_entry() and "extra" not in entry["slots"][0]  # type: ignore[index]
    for endpoint in ("9.9.9.9:51820", "8.8.8.8:51901"):
        with pytest.raises(connect.ConnectError, match="connect_slot_endpoint_invalid"):
            connect.manifest_entry(
                UUID(GERMANY_ID), [{**slots[0], "endpoint": endpoint}], "9.9.9.9"
            )


def test_parse_markers_ignores_everything_else() -> None:
    raw = b"row=absent\nnoise 9.9.9.9\nBAD KEY=1\nin_manifest=0\nlong=" + b"x" * 200 + b"\n"
    assert connect.parse_markers(raw) == {"row": "absent", "in_manifest": "0"}


class FakeHosts(connect.Hosts):
    """Scripted hosts: records every remote program by name and answers with fixed markers."""

    def __init__(
        self, node: BetaNode, status: dict[str, str], fail: str | None = None, helper_sha: str = ""
    ) -> None:
        super().__init__(node)
        self.status = status
        self.fail = fail
        self.helper_sha = helper_sha
        self.calls: list[str] = []
        self.payloads: dict[str, bytes] = {}
        self.names = {id(value): name for name, value in connect.PROGRAMS.items()}

    def _answer(self, program: str, args: tuple[str, ...], payload: bytes | None) -> bytes:
        name = self.names[id(program)]
        self.calls.append(name)
        if payload is not None:
            self.payloads[name] = payload
        if self.fail == name:
            raise connect.ConnectError(f"connect_remote_step_failed:9:{name}")
        answers = {
            "API_STATUS": "\n".join(f"{k}={v}" for k, v in self.status.items())
            + f"\nhelper_sha={self.helper_sha}\n",
            "API_READ_CREDENTIAL": json.dumps(
                {"version": 1, "node_id": GERMANY_ID, "credential": CREDENTIAL}
            ),
            "API_CADDY_ALLOW": "caddy_allows_node backup=Caddyfile.pre-add-x\n",
            "API_MERGE_MANIFEST": "manifest_merged backup=window-manifest.json.pre-add-x\n",
            "API_CLEANUP_CREDENTIAL": "credential_source_removed\n",
            "API_RESTORE_FILES": "pre_add_files_restored\n",
            "API_ACTIVATE_PREFLIGHT": "connect_activation_preflight_passed\n",
            "API_SET_STATUS": "node_row_" + (args[3] if len(args) > 3 else "x") + "\n",
            "API_WAIT_RUNTIME": "beta_node_authenticated_inventory_and_poll\n",
            "NODE_READ_SLOTS": json.dumps({"version": 1, "slots": germany_entry()["slots"]}),
            "NODE_STAGE": "node_configuration_staged\n",
            "NODE_UNSTAGE": "node_configuration_unstaged\n",
            "NODE_PREFLIGHT": "beta_node_activation_preflight_passed\n",
            "NODE_PREFLIGHT_BOUND": "node_bound_files_verified\n",
            "NODE_READ_AGENT_ID": f"node_id={GERMANY_ID}\n",
            "NODE_START": "beta_node_agent_active\n",
            "NODE_ENABLE_BOOT": "beta_node_agent_enabled_at_boot\n",
            "NODE_STOP": "beta_node_agent_stopped\n",
        }
        return answers[name].encode()

    def api(
        self,
        program: str,
        args: tuple[str, ...] = (),
        payload: bytes | None = None,
        label: str = "api",
    ) -> bytes:
        return self._answer(program, args, payload)

    def node_run(
        self,
        program: str,
        args: tuple[str, ...] = (),
        payload: bytes | None = None,
        label: str = "node",
    ) -> bytes:
        return self._answer(program, args, payload)

    def api_shell(
        self, command: str, payload: bytes | None = None, label: str = "api_shell"
    ) -> bytes:
        self.calls.append("shell:" + label)
        if self.fail == "shell:" + label:
            raise connect.ConnectError("connect_remote_step_failed:9")
        if label == "provision":
            return json.dumps(
                {
                    "created": True,
                    "node_id": GERMANY_ID,
                    "status": "disabled",
                    "credential_path": "/run/x",
                }
            ).encode()
        return b"helper_updated\n"


def test_register_dry_run_changes_nothing() -> None:
    hosts = FakeHosts(germany(), {"row": "absent"})
    result = connect.register(hosts, False)
    assert result["registered"] is False and hosts.calls == ["API_STATUS"]


def test_register_refuses_an_existing_row() -> None:
    with pytest.raises(connect.ConnectError, match="connect_register_row_exists"):
        connect.register(FakeHosts(germany(), {"row": "disabled"}), True)


def test_register_creates_one_disabled_node_with_the_deterministic_key() -> None:
    local = __import__("hashlib").sha256(connect.HELPER_SOURCE.read_bytes()).hexdigest()
    hosts = FakeHosts(germany(), {"row": "absent"}, helper_sha=local)
    result = connect.register(hosts, True)
    assert (
        result["registered"] is True
        and result["node_id"] == GERMANY_ID
        and result["status"] == "disabled"
    )
    assert hosts.calls == ["API_STATUS", "shell:provision"]


def test_register_updates_only_the_known_previous_helper() -> None:
    hosts = FakeHosts(germany(), {"row": "absent"}, helper_sha=connect.HELPER_PREVIOUS_SHA256)
    connect.register(hosts, True)
    assert hosts.calls == [
        "API_STATUS",
        "shell:helper_upload",
        "shell:helper_install",
        "shell:provision",
    ]
    unknown = FakeHosts(germany(), {"row": "absent"}, helper_sha="0" * 64)
    with pytest.raises(connect.ConnectError, match="connect_helper_unexpected_version"):
        connect.register(unknown, True)
    assert unknown.calls == ["API_STATUS"]


def test_helper_update_script_checks_hashes_and_rolls_back() -> None:
    captured: list[str] = []

    class Recording(FakeHosts):
        def api_shell(
            self, command: str, payload: bytes | None = None, label: str = "api_shell"
        ) -> bytes:
            captured.append(command)
            return b"helper_updated\n"

    connect.deploy_helper(Recording(germany(), {}), connect.HELPER_PREVIOUS_SHA256)
    script = shlex.split(captured[1])[2]
    assert (
        connect.HELPER_PREVIOUS_SHA256 in script
        and "helper_rolled_back" in script
        and "cp -a --" in script
    )


BOUND = {"row": "disabled", "credential": "present", "in_manifest": "0"}


def test_bind_dry_run_only_reads() -> None:
    hosts = FakeHosts(germany(), BOUND)
    result = connect.bind(hosts, False)
    assert result["bound"] is False and hosts.calls == ["API_STATUS", "NODE_READ_SLOTS"]


@pytest.mark.parametrize(
    "status",
    [
        {"row": "absent"},
        {"row": "active", "credential": "present"},
        {"row": "disabled", "credential": "absent"},
    ],
)
def test_bind_requires_a_registered_disabled_node_with_its_credential(
    status: dict[str, str],
) -> None:
    with pytest.raises(
        connect.ConnectError, match="connect_bind_requires_registered_disabled_node"
    ):
        connect.bind(FakeHosts(germany(), status), True)


def test_bind_runs_the_steps_in_order_and_never_returns_the_credential() -> None:
    hosts = FakeHosts(germany(), BOUND)
    result = connect.bind(hosts, True)
    assert hosts.calls == [
        "API_STATUS",
        "NODE_READ_SLOTS",
        "API_READ_CREDENTIAL",
        "NODE_STAGE",
        "API_CADDY_ALLOW",
        "API_MERGE_MANIFEST",
        "NODE_PREFLIGHT_BOUND",
        "API_CLEANUP_CREDENTIAL",
    ]
    assert result["bound"] is True and result["status"] == "disabled"
    assert CREDENTIAL not in json.dumps(result)
    assert json.loads(hosts.payloads["API_MERGE_MANIFEST"]) == germany_entry()
    assert (
        CREDENTIAL.encode() not in hosts.payloads["API_MERGE_MANIFEST"]
    )  # the manifest never carries the credential
    staged = json.loads(hosts.payloads["NODE_STAGE"])
    assert staged["api_origin"] == connect.API_ORIGIN and staged["slot_count"] == 1


@pytest.mark.parametrize(
    "failing",
    ["API_CADDY_ALLOW", "API_MERGE_MANIFEST", "NODE_PREFLIGHT_BOUND", "API_CLEANUP_CREDENTIAL"],
)
def test_bind_failure_restores_the_files_and_unstages_the_node(failing: str) -> None:
    hosts = FakeHosts(germany(), BOUND, fail=failing)
    with pytest.raises(connect.ConnectError, match="connect_bind_failed_rolled_back"):
        connect.bind(hosts, True)
    assert hosts.calls[-2:] == ["API_RESTORE_FILES", "NODE_UNSTAGE"]


def test_bind_failure_before_any_change_has_nothing_to_roll_back() -> None:
    hosts = FakeHosts(germany(), BOUND, fail="NODE_STAGE")
    with pytest.raises(connect.ConnectError, match="connect_bind_failed_rolled_back"):
        connect.bind(hosts, True)
    assert "API_CADDY_ALLOW" not in hosts.calls and "NODE_UNSTAGE" not in hosts.calls


ACTIVATE_STATE = {"row": "disabled", "credential": "absent", "in_manifest": "1"}


def test_activate_dry_run_runs_only_preflights() -> None:
    hosts = FakeHosts(germany(), ACTIVATE_STATE)
    assert connect.activate(hosts, False) == {"activated": False, "preflight": "passed"}
    assert not {"API_SET_STATUS", "NODE_START", "NODE_ENABLE_BOOT"} & set(hosts.calls)


@pytest.mark.parametrize(
    "status",
    [
        {"row": "disabled", "credential": "present", "in_manifest": "1"},
        {"row": "disabled", "credential": "absent", "in_manifest": "0"},
        {"row": "active", "credential": "absent", "in_manifest": "1"},
    ],
)
def test_activate_requires_a_bound_disabled_node(status: dict[str, str]) -> None:
    with pytest.raises(connect.ConnectError, match="connect_activate_requires_bound_disabled_node"):
        connect.activate(FakeHosts(germany(), status), True)


def test_activate_enables_the_row_then_the_agent_then_boot_start() -> None:
    hosts = FakeHosts(germany(), ACTIVATE_STATE)
    result = connect.activate(hosts, True)
    tail = hosts.calls[hosts.calls.index("API_SET_STATUS") :]
    assert tail == ["API_SET_STATUS", "NODE_START", "API_WAIT_RUNTIME", "NODE_ENABLE_BOOT"]
    assert result["status"] == "active" and result["enabled_at_boot"] is True


@pytest.mark.parametrize("failing", ["NODE_START", "API_WAIT_RUNTIME", "NODE_ENABLE_BOOT"])
def test_activate_failure_stops_the_agent_and_disables_the_row(failing: str) -> None:
    hosts = FakeHosts(germany(), ACTIVATE_STATE, fail=failing)
    with pytest.raises(connect.ConnectError, match="connect_activate_failed_rolled_back"):
        connect.activate(hosts, True)
    assert hosts.calls[-2:] == ["NODE_STOP", "API_SET_STATUS"]


def test_rollback_stops_the_agent_before_disabling_the_row() -> None:
    hosts = FakeHosts(germany(), {})
    result = connect.rollback(hosts, True)
    assert hosts.calls == ["NODE_READ_AGENT_ID", "NODE_STOP", "API_SET_STATUS"]
    assert result["status"] == "disabled"


def run_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        connect, "load_inventory", lambda path: parse_inventory(INVENTORY, allow_non_global=True)
    )
    code = connect.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_main_refuses_the_existing_helsinki_node(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _out, err = run_main(
        monkeypatch, capsys, "register", "--node", "detourvpn-beta-helsinki", "--execute"
    )
    assert code == 1 and json.loads(err)["error"] == "connect_existing_node_refused"


def test_main_status_is_read_only_and_hides_the_helper_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeHosts(germany(), {"row": "absent", "in_manifest": "0"})
    monkeypatch.setattr(connect, "Hosts", lambda node: fake)
    code, out, _err = run_main(monkeypatch, capsys, "status", "--node", "detourvpn-beta-germany")
    report = json.loads(out)
    assert (
        code == 0
        and report["row"] == "absent"
        and "helper_sha" not in report
        and "9.9.9.9" not in out
    )


def test_main_default_is_a_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeHosts(germany(), {"row": "absent"})
    monkeypatch.setattr(connect, "Hosts", lambda node: fake)
    code, out, _err = run_main(monkeypatch, capsys, "register", "--node", "detourvpn-beta-germany")
    assert code == 0 and json.loads(out)["mode"] == "dry_run" and fake.calls == ["API_STATUS"]


def test_main_errors_use_fixed_codes_without_remote_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeHosts(germany(), {"row": "absent"}, fail="API_STATUS")
    monkeypatch.setattr(connect, "Hosts", lambda node: fake)
    code, _out, err = run_main(monkeypatch, capsys, "register", "--node", "detourvpn-beta-germany")
    assert code == 1 and json.loads(err)["error"].startswith("connect_remote_step_failed")

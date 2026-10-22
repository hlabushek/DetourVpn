"""OPT-IN actual approved staging + Android acceptance. Never runs in ordinary CI.

Telegram delivery is explicitly the shared TEST fake. Auth, PostgreSQL, HTTP API,
agent, kernel gate, native AWG and the physical phone are real in this test.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy.orm import Session
from test_authentication import Harness, harness, login

from vpn_api.db.models import Tunnel, TunnelReservation, VPNNode
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeRegistry
from vpn_api.tunnels.admission import NodeProfile

__all__ = ["harness"]
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("VPN_I012_STAGING_APPROVED") != "45.10.166.106",
        reason="Explicit I-012 staging approval and local sudo grant required",
    ),
]
ROOT = Path(__file__).resolve().parents[3]
PRIVATE = Path(os.environ.get("LOCALAPPDATA", "")) / "ProjectVpn/i012-private"
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe"
PACKAGE = "org.example.vpnpoc"
CONTROL = "/opt/project-vpn/acceptance/i012_control.py"


def safe_phone_failure(facts: dict[str, Any]) -> dict[str, Any]:
    """Evidence is a fixed vocabulary, never a raw instrumentation/API response."""
    stages = {
        "initialize",
        "device_registration",
        "tunnel_request",
        "waiting_node_ack",
        "native_connect",
        "profile_parse",
        "native_activity",
        "native_start",
        "native_up",
        "staging_egress",
        "fresh_handshake",
        "api_revoke",
        "waiting_agent_restart",
        "waiting_lease_expiry",
        "lease_cutoff_without_control",
        "complete",
        "revoke_precondition",
        "waiting_revoke_ack",
        "native_state_at_cutoff",
        "traffic_cutoff",
        "cutoff_before_expiry",
        "quota_settlement",
        "native_disconnect",
        "network_restore",
    }
    stage = facts.get("stage")
    result: dict[str, Any] = {
        "stage": stage if isinstance(stage, str) and stage in stages else "unknown"
    }
    for key in (
        "native_interface_up",
        "profile_parsed",
        "connect_button_clicked",
        "permission_required",
        "permission_still_required",
        "keyguard_locked",
        "ui_reported_failure",
        "config_after_node_ack",
        "device_key_proof_accepted",
        "native_up_and_staging_egress",
        "fresh_handshake",
        "baseline_egress_differs_from_staging",
        "native_up_at_cutoff",
        "lease_valid_before_revoke",
        "api_revoke_stops_traffic",
        "agent_restart_stops_traffic",
        "lease_expiry_stops_traffic",
        "quota_settled_once",
        "disconnect_restores_network",
        "native_down_after_disconnect",
    ):
        if type(facts.get(key)) is bool:
            result[key] = facts[key]
    for key in ("last_http_status", "egress_http_status"):
        value = facts.get(key)
        if type(value) is int and 100 <= value <= 599:
            result[key] = value
    code = facts.get("api_error")
    for key in ("last_tunnel_state", "revoke_response_state"):
        value = facts.get(key)
        if isinstance(value, str) and value in {
            "admission_pending",
            "provision_pending",
            "active",
            "revoke_pending",
            "revoked",
        }:
            result[key] = facts[key]
    egress_result = facts.get("egress_result")
    if isinstance(egress_result, str) and egress_result in {"unavailable", "staging", "other"}:
        result["egress_result"] = facts["egress_result"]
    if isinstance(code, str) and code in {
        "tunnel_node_unavailable",
        "tunnel_profile_unavailable",
        "device_key_proof_required",
        "device_key_proof_invalid",
        "insufficient_quota",
        "session_device_mismatch",
        "tunnel_node_capacity",
        "tunnel_config_not_ready",
        "rate_limited",
        "device_revoked",
        "tunnel_exists",
        "unexpected_error",
    }:
        result["api_error"] = code
    return result


class PhoneAcceptanceFailure(RuntimeError):
    def __init__(self, facts: dict[str, Any]) -> None:
        self.facts = safe_phone_failure(facts)
        super().__init__("phone_lifecycle_failed_at_" + str(self.facts["stage"]))


def pending_control_actions(state: dict[str, Any], seen: set[str]) -> list[str]:
    """Level-triggered, fixed actions survive changes to the displayed phone phase."""
    mapping = {
        "restart_agent": "waiting_agent_restart",
        "pause_control": "waiting_lease_expiry",
        "restore_control": "lease_cutoff_without_control",
    }
    events = state.get("control_events")
    if not isinstance(events, list) or len(events) > 3:
        return []
    actions = []
    for event in events:
        if isinstance(event, str) and event in mapping:
            action = mapping[event]
            if action not in seen and action not in actions:
                actions.append(action)
    return actions


def run(args: list[str], data: bytes | None = None, timeout: int = 30) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed binaries/actions; no local shell
        args, input=data, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError("acceptance_command_failed")
    return result.stdout


def ssh_args() -> list[str]:
    return [
        "ssh",
        "-i",
        str(PRIVATE / "staging-key"),
        "-o",
        "UserKnownHostsFile=//wsl.localhost/kali-linux/home/ad/.ssh/known_hosts",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
    ]


def remote(action: str, data: bytes | None = None) -> dict[str, Any]:
    if action not in {"status", "prepare", "start", "profile", "stop", "expire", "restart-agent"}:
        raise ValueError("unsupported_control_action")
    raw = run(
        [*ssh_args(), "vpn-deploy@45.10.166.106", f"sudo -n {CONTROL} {action}"], data, timeout=250
    )
    result: dict[str, Any] = json.loads(raw)
    return result


def adb(*args: str, data: bytes | None = None, timeout: int = 30) -> bytes:
    return run([str(ADB), *args], data, timeout)


def transport() -> subprocess.Popen[bytes]:
    process = subprocess.Popen(  # noqa: S603 - fixed approved host/loopback forwarding
        [
            *ssh_args(),
            "-N",
            "-T",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=5",
            "-R",
            "127.0.0.1:18765:127.0.0.1:18766",
            "vpn-deploy@45.10.166.106",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    if process.poll() is not None:
        raise RuntimeError("staging_transport_not_available")
    return process


def phone(token: str, mode: str, on_stage: Callable[[str], None]) -> dict[str, Any]:
    expiry, restart = mode == "lease_expiry", mode == "agent_restart"
    adb("shell", "am", "force-stop", PACKAGE)
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "run-as", PACKAGE, "mkdir", "-p", "files")
    adb(
        "shell",
        "-T",
        "run-as org.example.vpnpoc sh -c 'cat > files/i012-access-token'",
        data=token.encode("ascii"),
    )
    adb("shell", "run-as", PACKAGE, "rm", "-f", "files/i012-result.json")
    process = subprocess.Popen(  # noqa: S603 - fixed explicit instrumentation, no shell on host
        [
            str(ADB),
            "shell",
            "am",
            "instrument",
            "-w",
            "-e",
            "lifecycle",
            "true",
            "-e",
            "leaseExpiry",
            "true" if expiry else "false",
            "-e",
            "agentRestart",
            "true" if restart else "false",
            PACKAGE + ".test/org.example.vpnpoc.PocDeviceTests",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    seen: set[str] = set()
    deadline = time.monotonic() + 170
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise RuntimeError("phone_scenario_deadline")
            try:
                state = json.loads(
                    adb("exec-out", "run-as", PACKAGE, "cat", "files/i012-result.json")
                )
            except (ValueError, RuntimeError, subprocess.SubprocessError):
                state = {}
            for action in pending_control_actions(state, seen):
                on_stage(action)
                seen.add(action)
            time.sleep(0.25)
        raw, _ = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    facts: dict[str, Any] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith("INSTRUMENTATION_RESULT: lifecycle_facts="):
            facts = json.loads(line.split("=", 1)[1])
    # The instrumentation emits only fixed labels and booleans. Never echo raw Android output.
    expected = {
        "device_key_proof_accepted",
        "config_after_node_ack",
        "native_up_and_staging_egress",
        "fresh_handshake",
        "quota_settled_once",
        "disconnect_restores_network",
        "lease_expiry_stops_traffic"
        if expiry
        else "agent_restart_stops_traffic"
        if restart
        else "api_revoke_stops_traffic",
        "baseline_egress_differs_from_staging",
    }
    if facts.get("stage") != "complete" or not all(facts.get(key) is True for key in expected):
        # Android emitted only fixed labels and status integers, no raw API/exception data.
        failure = PhoneAcceptanceFailure(facts)
        print(json.dumps(failure.facts), flush=True)
        raise failure
    return {key: True for key in sorted(expected)}


def test_i012_live_staging_and_physical_phone(harness: Harness) -> None:
    h = harness
    assert os.name == "nt", "The reviewed ADB/SSH controller is Windows-only"
    assert (PRIVATE / "staging-key").is_file(), (
        "Use run_i012_staging.ps1 for private ACL preparation"
    )
    # Read-only guard is first: an expired/missing grant cannot start a window.
    status = remote("status")
    assert status.get("endpoint_active") is False and status.get("agent_active") is False
    profile_path = PRIVATE / "node-profile.json"
    h.settings.tunnel_profile_file = profile_path
    h.settings.initial_quota_bytes = 16777216
    node = NodeRegistry(h.engine).provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="i012-staging-window", region="staging")
    )
    assert node.secret is not None
    bootstrap = {
        "node_id": str(node.node_id),
        "runtime_id": str(uuid4()),
        "credential": node.secret,
        "bootstrap_public_key": base64.b64encode(
            X25519PrivateKey.generate().public_key().public_bytes_raw()
        ).decode(),
    }
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender),
            host="127.0.0.1",
            port=18766,
            access_log=False,
            log_config=None,
            log_level="critical",
            timeout_graceful_shutdown=5,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    tunnel: subprocess.Popen[bytes] | None = None
    report: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "telegram_delivery": "TEST FAKE ONLY",
        "real_postgresql": True,
        "real_http_api": True,
        "scenarios": {},
        "cleanup_complete": False,
    }
    started = False
    sequence_complete = False
    stage = "device_preflight"
    cleanup: dict[str, Any] = {}
    try:
        devices = adb("devices").decode()
        assert sum(line.endswith("\tdevice") for line in devices.splitlines()) == 1
        adb(
            "install",
            "-r",
            str(ROOT / "apps/android/app/build/outputs/apk/debug/app-debug.apk"),
            timeout=60,
        )
        adb(
            "install",
            "-r",
            str(
                ROOT
                / "apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
            ),
            timeout=60,
        )
        adb("reverse", "tcp:18766", "tcp:18766")
        thread.start()
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        tunnel = transport()
        stage = "endpoint_start"
        remote("prepare", json.dumps(bootstrap).encode("ascii"))
        started = True
        remote("start")
        selected = remote("profile")  # Secret channel: never print/slurp to user-facing output.
        NodeProfile.model_validate(selected)
        profile_path.write_text(json.dumps(selected), encoding="ascii")
        # The profile inherits the user-only ACL outside the OneDrive checkout.
        stage = "agent_negotiation"
        negotiated = False
        for _ in range(40):
            with Session(h.engine) as session:
                current_node = session.get(VPNNode, node.node_id)
                negotiated = (
                    current_node is not None
                    and current_node.peer_protocol_version == 2
                    and str(current_node.peer_runtime_id) == bootstrap["runtime_id"]
                    and current_node.last_seen_at is not None
                )
            if negotiated:
                break
            time.sleep(0.25)
        if not negotiated:
            observed = remote("status")
            report["agent_active_at_failure"] = observed.get("agent_active") is True
            report["agent_failure_stage"] = observed.get("agent_failure_stage")
            raise RuntimeError("agent_negotiation_not_observed")
        report["real_agent_negotiated"] = True
        offline_test = {"paused": False, "restored": False}

        def on_stage(current: str) -> None:
            nonlocal tunnel
            if current == "waiting_agent_restart":
                assert remote("restart-agent").get("agent_restarted") is True
                report["actual_agent_restart_verified"] = True
                print("I-012: actual agent restarted; waiting for peer reconciliation.", flush=True)
            if current == "waiting_lease_expiry":
                with Session(h.engine) as session:
                    active = (
                        session.query(Tunnel)
                        .filter(Tunnel.node_id == node.node_id, Tunnel.state == "active")
                        .one()
                    )
                    assert active.lease_deadline is not None
                    assert (active.lease_deadline - datetime.now(UTC)).total_seconds() > 5
                assert tunnel is not None
                tunnel.terminate()
                tunnel.wait(timeout=10)
                tunnel = None
                offline_test["paused"] = True
                print(
                    "I-012: control-plane transport closed; kernel lease remains authoritative.",
                    flush=True,
                )
            if current == "lease_cutoff_without_control":
                assert offline_test["paused"] and tunnel is None
                tunnel = transport()
                offline_test["restored"] = True
                print(
                    "I-012: offline cutoff observed on phone; transport restored for settlement.",
                    flush=True,
                )

        for index, mode in enumerate(("api_revoke", "agent_restart", "lease_expiry")):
            stage = "physical_phone_" + mode
            print("I-012: physical phone scenario " + mode + ".", flush=True)
            tokens = login(h, subject=2**40 + 100 + index)
            try:
                report["scenarios"][mode] = phone(tokens.access_token, mode, on_stage)
            except PhoneAcceptanceFailure as failure:
                report["phone_failure"] = failure.facts
                # Let the phone's finally-block revoke settle, preserving bounded byte
                # diagnostics before the disposable DB and root journal are removed.
                for _ in range(8):
                    with Session(h.engine) as session:
                        rows = (
                            session.query(Tunnel, TunnelReservation)
                            .join(TunnelReservation, TunnelReservation.tunnel_id == Tunnel.id)
                            .all()
                        )
                        report["lease_diagnostics"] = [
                            {
                                "state": tunnel_row.state,
                                "budget_bytes": reservation.amount_bytes,
                                "charged_bytes": reservation.charged_bytes,
                                "observed_bytes": reservation.observed_bytes,
                                "settled": reservation.settled_at is not None,
                            }
                            for tunnel_row, reservation in rows
                        ]
                    if all(row["settled"] for row in report["lease_diagnostics"]):
                        break
                    time.sleep(0.25)
                raise
        assert all(offline_test.values())
        report["control_plane_loss_and_recovery_verified"] = True
        sequence_complete = True
        stage = "cleanup"
    finally:
        # Remote stop precedes removal of transport/API so the final state is observed.
        try:
            cleanup = remote("expire")
            report["cleanup_complete"] = cleanup.get("cleanup_complete") is True
            report["control_grant_removed"] = cleanup.get("control_grant_removed") is True
            report["cleanup_receipt"] = cleanup.get("cleanup_receipt", {})
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            report["cleanup_complete"] = False
        if tunnel is not None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=15)
        try:
            adb("shell", "am", "force-stop", PACKAGE)
            adb("shell", "run-as", PACKAGE, "rm", "-f", "files/i012-access-token")
            adb("reverse", "--remove", "tcp:18766")
            adb("uninstall", PACKAGE + ".test")
        except (RuntimeError, subprocess.SubprocessError):
            report["phone_cleanup_failed"] = True
        profile_path.unlink(missing_ok=True)
        passed = (
            sequence_complete
            and report.get("real_agent_negotiated") is True
            and report.get("actual_agent_restart_verified") is True
            and report.get("control_plane_loss_and_recovery_verified") is True
            and len(report["scenarios"]) == 3
            and report["cleanup_complete"]
            and report.get("control_grant_removed")
            and not report.get("phone_cleanup_failed")
            and not thread.is_alive()
        )
        report["passed"] = bool(passed)
        if not passed:
            report["failed_at"] = stage
        destination = (
            ROOT
            / "docs/evidence"
            / (
                "i-012-staging-lifecycle.json"
                if passed
                else "i-012-staging-lifecycle-failure-"
                + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                + ".json"
            )
        )
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert started and report["passed"], (
        "I-012 live acceptance did not pass; inspect sanitized evidence"
    )

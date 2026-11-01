"""OPT-IN physical Android + approved two-host drill. Telegram is TEST FAKE ONLY.

Production app/API contain none of the loopback-only coordination routes below.
No altered database clocks, fake node ACKs, device keys or payload logging.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_authentication import Harness, harness, issue
from test_i012_live import ROOT, adb

from vpn_api.auth.contracts import StartResponse
from vpn_api.db.models import (
    LoginAttempt,
    PeerCommand,
    QuotaLedger,
    Tunnel,
    TunnelReservation,
    VPNNode,
)
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeError, NodeRegistry
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels import routes as tunnel_routes
from vpn_api.tunnels.admission import NodeProfile
from vpn_api.tunnels.lifecycle import LifecycleAck, LifecycleCommands
from vpn_api.tunnels.lifecycle import apply_ack as lifecycle_apply_ack
from vpn_api.tunnels.lifecycle import commands as lifecycle_commands

__all__ = ["harness"]
HOSTS = ("45.10.166.106", "150.241.99.227")


def cancel_job_and_wait(cancel: threading.Event, thread: threading.Thread | None) -> None:
    """Do not overlap a running host operation with final cleanup."""
    cancel.set()
    if thread is not None:
        thread.join()


def check_job_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RuntimeError("job_cancelled")


PRIVATE = Path(os.environ.get("LOCALAPPDATA", "")) / "ProjectVpn/i019-private"
CONTROL = "/opt/project-vpn/acceptance/i019_control.py"
PACKAGE = "org.example.vpnclient"
PROBE_PACKAGE = "org.example.vpnpoc.i028"
PROBE_TEST_PACKAGE = PROBE_PACKAGE + ".test"
PROBE_RESULTS = frozenset(
    {
        "node_a",
        "node_b",
        "other_address",
        "dns",
        "timeout",
        "route",
        "tls",
        "connect",
        "permission",
        "io",
        "http_error",
        "instrumentation_missing",
        "unexpected_result",
        "adb_error",
        "probe_task_error",
    }
)
MAX_EGRESS_PROBE_RECORDS = 32
TRIGGER_RESULTS = frozenset({"connected", "timeout", "route", "permission", "connect", "io"})
MAX_AGENT_EVENTS = 32
I019_API_ERROR_CODES = frozenset(
    {
        "agent_unavailable",
        "agent_unauthorized",
        "agent_runtime_mismatch",
        "agent_capability_mismatch",
        "agent_replay",
        "tunnel_profile_unavailable",
        "storage_unavailable",
        "invalid_request",
        "peer_protocol_downgrade_forbidden",
        "agent_inventory_invalid",
    }
)
I019_API_EXCEPTION_TYPES = frozenset(
    {
        "ResponseValidationError",
        "ValidationError",
        "IntegrityError",
        "OperationalError",
        "ProgrammingError",
        "TypeError",
        "ValueError",
        "KeyError",
        "AttributeError",
        "AssertionError",
    }
)


class I019ApiFailureObserver:
    """TEST ONLY: persist fixed endpoint/status/error categories, never response bodies."""

    def __init__(self, app: Any, *, report: dict[str, Any]) -> None:
        self.app, self.report = app, report

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        endpoints = {
            "/api/internal/v1/agent/peers/lifecycle/poll": "poll",
            "/api/internal/v1/agent/peers/lifecycle/ack": "ack",
        }
        endpoint = endpoints.get(scope.get("path")) if scope.get("type") == "http" else None
        if endpoint is None:
            await self.app(scope, receive, send)
            return

        response: dict[str, Any] = {"status": 0, "body": bytearray()}

        async def observe_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                response["status"] = message.get("status", 0)
            elif message.get("type") == "http.response.body":
                chunk = message.get("body", b"")
                if len(response["body"]) + len(chunk) <= 2048:
                    response["body"].extend(chunk)
            await send(message)

        exception_type = ""
        try:
            await self.app(scope, receive, observe_send)
        except Exception as failure:
            candidate = type(failure).__name__
            exception_type = candidate if candidate in I019_API_EXCEPTION_TYPES else "other"
            if response["status"] == 0:
                response["status"] = 500
            raise
        finally:
            status = response["status"]
            if isinstance(status, int) and status >= 500:
                event: dict[str, Any] = {"endpoint": endpoint, "status": status}
                if exception_type:
                    event["exception"] = exception_type
                try:
                    body = json.loads(bytes(response["body"]))
                except (ValueError, TypeError):
                    body = None
                error = body.get("error") if isinstance(body, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                if isinstance(code, str) and code in I019_API_ERROR_CODES:
                    event["error_code"] = code
                prior = self.report.setdefault("api_failures", [])
                if event not in prior:
                    prior.append(event)
                    del prior[:-6]


FACTS = frozenset(
    {
        "b_initial_handshake_egress",
        "b_initial_disconnect_restores_network",
        "a_handshake_egress",
        "early_recovery_denied",
        "second_peer_denied",
        "kernel_expiry_without_agent_or_api",
        "normal_network_after_expiry",
        "first_recovery",
        "a_restart_egress",
        "node_failure_cutoff_before_expiry",
        "normal_network_after_node_failure",
        "second_recovery",
        "b_handshake_egress",
        "return_a_does_not_revive_old_peer",
        "b_disconnect_restores_network",
    }
)
CONNECT_STAGES = frozenset(
    f"{attempt}_{step}"
    for attempt in ("b_initial_connect", "a_first_connect", "restart_a", "b_connect")
    for step in ("catalog", "admission", "handshake", "egress")
)
FAILURE_STAGES = CONNECT_STAGES | {
    "b_initial_connect",
    "a_first_connect",
    "b_connect",
    "initialize",
    "authentication",
    "b_initial_disconnect",
    "negative_early_recovery",
    "negative_second_peer",
    "kernel_expiry",
    "first_recovery",
    "restart_a",
    "node_failure_cutoff",
    "second_recovery",
    "return_a_while_b_active",
    "b_disconnect",
    "ledger_verify",
}
DIAGNOSTIC_CODES = frozenset(
    {
        "none",
        "permission_denied",
        "insufficient_quota",
        "device_revoked",
        "session_expired",
        "unauthorized",
        "device_limit_reached",
        "session_device_mismatch",
        "device_conflict",
        "tunnel_node_capacity",
        "tunnel_recovery_not_ready",
        "tunnel_node_unavailable",
        "tunnel_profile_unavailable",
        "admission_closed",
        "lease_expired",
        "remote_revoked",
        "revoke_unconfirmed",
        "native_disconnect_failed",
        "network_unavailable",
        "api_unavailable",
        "native_unavailable",
        "native_down",
        "rate_limited",
        "invalid_response",
        "not_found",
    }
)
NODE_REASONS = frozenset(
    {"available", "disabled", "heartbeat_stale", "runtime_mismatch", "capacity"}
)


def normal_android_network(raw: str) -> dict[str, bool]:
    """Only current default capabilities survive; never log the connectivity dump."""
    match = re.search(r"Active default network:\s*(\d+)", raw)
    rows = []
    if match is not None:
        pattern = r"network\{" + match[1] + r"\}"
        rows = [
            line
            for line in raw.splitlines()
            if "NetworkAgentInfo" in line and re.search(pattern, line)
        ]
    result = {
        "identified": False,
        "vpn": False,
        "validated": False,
        "not_vpn": False,
        "normal": False,
    }
    if len(rows) != 1:
        return result
    transports = re.search(r"Transports:\s*([A-Z| ]+)", rows[0])
    if transports is None:
        return result
    result.update(
        identified=True,
        vpn="VPN" in transports[1].replace("|", " ").split(),
        validated=re.search(r"\bVALIDATED\b", rows[0]) is not None,
        not_vpn=re.search(r"\bNOT_VPN\b", rows[0]) is not None,
    )
    result["normal"] = result["not_vpn"] and result["validated"] and not result["vpn"]
    return result


def sanitized_egress_probe(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, allowed in {
        "stage": FAILURE_STAGES,
        "phase": {"request", "response", "body"},
        "result": {
            "pending",
            "http_status",
            "invalid_body",
            "node_a",
            "node_b",
            "other_address",
            "dns",
            "timeout",
            "route",
            "tls",
            "connect",
            "permission",
            "io",
            "http_error",
            "instrumentation_missing",
            "unexpected_result",
            "adb_error",
            "probe_task_error",
            "unavailable",
            "socket_error",
            "tls_error",
            "other_error",
        },
    }.items():
        candidate = value.get(key)
        result[key] = candidate if isinstance(candidate, str) and candidate in allowed else "other"
    for key, lower, upper in (("http_status", 100, 599), ("os_error", -4096, 65535)):
        candidate = value.get(key)
        if type(candidate) is int and lower <= candidate <= upper:
            result[key] = candidate
    trigger = value.get("trigger")
    if isinstance(trigger, str):
        result["trigger"] = trigger if trigger in TRIGGER_RESULTS else "unavailable"
    return result


def append_egress_probe(records: list[dict[str, Any]], value: dict[str, Any]) -> dict[str, Any]:
    """Append one sanitized probe record while keeping the receipt strictly bounded."""
    if len(records) >= MAX_EGRESS_PROBE_RECORDS:
        raise ValueError("i019_egress_probe_limit_reached")
    sanitized = sanitized_egress_probe(value)
    records.append(sanitized)
    return sanitized


class AgentEventRecorder:
    """Bounded test receipt events; peer IDs are replaced with per-run ordinals."""

    def __init__(self) -> None:
        self._peer_aliases: dict[UUID, int] = {}
        self.events: list[dict[str, Any]] = []
        self.truncated = False
        self._lock = threading.Lock()

    def record(
        self,
        kind: str,
        peer_id: UUID,
        operation: str,
        version: int,
        *,
        handler_returned: bool | None = None,
        applied: bool | None = None,
    ) -> None:
        if kind not in {"delivery", "ack"} or operation not in {"add_peer", "remove_peer"}:
            raise ValueError("i019_agent_event_invalid")
        if type(version) is not int or not 1 <= version <= 2**63 - 1:
            raise ValueError("i019_agent_event_invalid")
        if kind == "ack" and (type(handler_returned) is not bool or type(applied) is not bool):
            raise ValueError("i019_agent_event_invalid")
        with self._lock:
            if len(self.events) >= MAX_AGENT_EVENTS:
                self.truncated = True
                return
            if peer_id not in self._peer_aliases:
                self._peer_aliases[peer_id] = len(self._peer_aliases) + 1
            event: dict[str, Any] = {
                "kind": kind,
                "peer_ordinal": self._peer_aliases[peer_id],
                "operation": operation,
                "version": version,
            }
            if kind == "ack":
                event["handler_returned"] = handler_returned
                event["applied"] = applied
            self.events.append(event)

    def snapshot(self) -> tuple[list[dict[str, Any]], bool]:
        with self._lock:
            return [dict(event) for event in self.events], self.truncated


def independent_egress_result(output: bytes) -> str:
    """Parse only the instrumentation's fixed category; discard all raw output."""
    matches = re.findall(
        rb"^INSTRUMENTATION_RESULT: i019_egress_result=([a-z_]{1,24})\r?$",
        output,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        return "instrumentation_missing"
    candidate = matches[0].decode("ascii")
    return candidate if candidate in PROBE_RESULTS else "unexpected_result"


def independent_trigger_result(output: bytes) -> str:
    """Keep only one fixed trigger category, never raw instrumentation output."""
    matches = re.findall(
        rb"^INSTRUMENTATION_RESULT: i019_trigger_result=([a-z_]{1,24})\r?$",
        output,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        return "unavailable"
    candidate = matches[0].decode("ascii")
    return candidate if candidate in TRIGGER_RESULTS else "unavailable"


def sanitized_phone_failure(value: dict[str, Any]) -> dict[str, Any]:
    """Never persist arbitrary strings, nested values, IDs, exception text or configs."""
    result: dict[str, Any] = {}
    vocabularies = {
        "stage": FAILURE_STAGES,
        "phase": {"disconnected", "connecting", "connected", "reconnecting", "failed"},
        "error": DIAGNOSTIC_CODES,
        "nodes_error": DIAGNOSTIC_CODES,
    }
    for key, vocabulary in vocabularies.items():
        candidate = value.get(key)
        result[key] = (
            candidate if isinstance(candidate, str) and candidate in vocabulary else "other"
        )
    for key in ("has_lease", "remote_pending", "native_up", "native_network"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key, lower, upper in (("handshake_age", -1, 86400), ("remaining_ms", 0, 60000)):
        candidate = value.get(key)
        if type(candidate) is int and lower <= candidate <= upper:
            result[key] = candidate
    reasons = value.get("node_reasons")
    if isinstance(reasons, list) and len(reasons) <= 2:
        result["node_reasons"] = [
            reason if isinstance(reason, str) and reason in NODE_REASONS else "other"
            for reason in reasons
        ]
    probe = value.get("last_probe")
    if isinstance(probe, dict):
        result["last_probe"] = sanitized_egress_probe(probe)
    return result


class CleanupNotConfirmed(RuntimeError):
    """Carry only fixed cleanup checks, never remote output or exception text."""

    def __init__(self, message: str, phase: str, values: dict[str, Any]) -> None:
        super().__init__(message)
        self.phase = phase
        self.checks = {
            key: values[key]
            for key in (
                "cleanup_complete",
                "agent_resume_failed",
                "agent_state_query_failed",
                "agent_stop_command_ok",
                "endpoint_stop_command_ok",
                "receipt_read_failed",
                "forwarding_restored",
                "owned_resources_absent",
                "control_grant_removed",
                "endpoint_active",
                "agent_active",
            )
            if type(values.get(key)) is bool
        }

    def receipt(self) -> dict[str, Any]:
        return {"error": "cleanup_not_confirmed", "phase": self.phase, "checks": self.checks}


def finish_node_attempt(host: str, *, retain_grant: bool) -> dict[str, Any]:
    """A failed cleanup never qualifies for reuse; no grant renewal is performed."""
    result = remote(host, "stop" if retain_grant else "expire")
    if not all(
        result.get(key) is True
        for key in (
            "cleanup_complete",
            "forwarding_restored",
            "owned_resources_absent",
        )
    ):
        raise CleanupNotConfirmed("i019_attempt_cleanup_not_confirmed", "stop_result", result)
    if retain_grant:
        status = remote(host, "status")
        if status != {"endpoint_active": False, "agent_active": False}:
            raise CleanupNotConfirmed("i019_attempt_still_active", "status", status)
        result["control_grant_retained_until_original_deadline"] = True
    elif result.get("control_grant_removed") is not True:
        raise CleanupNotConfirmed("i019_grant_removal_not_confirmed", "grant", result)
    return result


def retain_session_grants(*, session: bool, passed: bool, diagnostic_only: bool) -> bool:
    return session and (not passed or diagnostic_only)


def finish_diagnostic_session() -> bool:
    """Try both hosts independently even if the first one's cleanup fails."""
    result: dict[str, Any] = {"issue": "I-019", "cleanup": {}}
    for host in HOSTS:
        try:
            result["cleanup"][host] = finish_node_attempt(host, retain_grant=False)
        except CleanupNotConfirmed as failure:
            result["cleanup"][host] = failure.receipt()
        except RemoteActionFailure:
            result["cleanup"][host] = {"error": "cleanup_not_confirmed", "phase": "remote_action"}
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            result["cleanup"][host] = {"error": "cleanup_not_confirmed"}
    result["passed"] = all(
        value.get("control_grant_removed") is True for value in result["cleanup"].values()
    )
    (ROOT / "docs/evidence/i-019-session-cleanup.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))
    return bool(result["passed"])


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("VPN_I019_STAGING_APPROVED") != ",".join(HOSTS),
        reason="Two-host staging grants and explicit physical I-019 opt-in required",
    ),
]


def ssh_args() -> list[str]:
    return [
        "ssh",
        "-i",
        str(Path(os.environ["USERPROFILE"]) / ".ssh/vpn_staging_20261019"),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
    ]


class RemoteActionFailure(RuntimeError):
    def __init__(self, host: str, action: str, diagnostics: dict[str, Any]) -> None:
        self.facts = {"host": host, "action": action, "diagnostics": diagnostics}
        super().__init__("i019_remote_action_failed")


def remote(host: str, action: str, data: bytes | None = None) -> dict[str, Any]:
    if host not in HOSTS or action not in {
        "status",
        "prepare",
        "start",
        "profile",
        "stop",
        "expire",
        "gate",
        "freeze-agent",
        "diagnose",
    }:
        raise ValueError("unsupported_staging_action")
    result = subprocess.run(  # noqa: S603 - fixed action/approved pair; secret stdin/stdout captured
        [*ssh_args(), "vpn-deploy@" + host, f"sudo -n {CONTROL} {action}"],
        input=data,
        capture_output=True,
        timeout=480 if action in {"stop", "expire"} else 250,
        check=False,
    )
    if result.returncode:
        diagnostics: dict[str, Any] = {"error": "diagnostics_unavailable"}
        if action != "diagnose":
            try:
                # Only the controller's allowlisted phase/cleanup receipt, never raw journal.
                diagnostics = remote(host, "diagnose")
            except (RuntimeError, ValueError, subprocess.SubprocessError):
                pass
        raise RemoteActionFailure(host, action, diagnostics)
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def transport(host: str) -> subprocess.Popen[bytes]:
    if host not in HOSTS:
        raise ValueError("unapproved_host")
    process = subprocess.Popen(  # noqa: S603 - loopback-only reverse channel on approved host
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
            "vpn-deploy@" + host,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    if process.poll() is not None:
        raise RuntimeError("i019_transport_unavailable")
    return process


def terminate(process: subprocess.Popen[bytes] | None) -> None:
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class ClockBoundExceeded(RuntimeError):
    def __init__(self, bound: float) -> None:
        self.upper_bound_seconds = bound
        super().__init__("staging_clock_bound_exceeded")


def clock_bound(host: str) -> float:
    """Bound offset after SSH authentication, using three real timestamp round trips."""
    if host not in HOSTS:
        raise ValueError("unapproved_host")
    command = (
        'python3 -u -c \'import sys,time; print("ready",flush=True); '
        "[(sys.stdin.readline(),print(time.time_ns(),flush=True)) for _ in range(3)]'"
    )
    process = subprocess.Popen(  # noqa: S603 - fixed, unprivileged read-only clock probe
        [*ssh_args(), "vpn-deploy@" + host, command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        assert process.stdin is not None and process.stdout is not None
        if pool.submit(process.stdout.readline).result(timeout=12).strip() != b"ready":
            raise RuntimeError("clock_probe_unavailable")
        bounds = []
        for _ in range(3):
            before = time.time_ns()
            process.stdin.write(b"tick\n")
            process.stdin.flush()
            server = int(pool.submit(process.stdout.readline).result(timeout=5))
            after = time.time_ns()
            bounds.append((abs(server - (before + after) / 2) + (after - before) / 2) / 1e9)
        bound = max(bounds)
        if bound > 1:
            raise ClockBoundExceeded(bound)
        return bound
    finally:
        terminate(process)
        pool.shutdown(wait=True)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


def test_i019_physical_two_host_failover(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness
    b_only = os.environ.get("VPN_I019_B_REUSE_DIAGNOSTIC") == "true"
    active_hosts = (HOSTS[1],) if b_only else HOSTS
    required_facts = (
        {
            "b_initial_handshake_egress",
            "b_initial_disconnect_restores_network",
            "b_handshake_egress",
            "b_disconnect_restores_network",
        }
        if b_only
        else FACTS
    )
    assert os.name == "nt"
    assert PRIVATE.is_dir()
    assert sum(line.endswith("\tdevice") for line in adb("devices").decode().splitlines()) == 1
    assert adb("shell", "getprop", "ro.kernel.qemu").strip() != b"1"
    assert b"tcp:18766" not in adb("reverse", "--list"), "Existing ADB forwarding must be reviewed"
    h.settings.initial_quota_bytes = 67108864
    manifest = PRIVATE / "nodes.json"
    h.settings.tunnel_profile_file = manifest
    report: dict[str, Any] = {
        "issue": "I-019",
        "mode": "b_reuse_diagnostic_only" if b_only else "full_two_host_failover",
        "measured_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "telegram_delivery": "TEST FAKE ONLY",
        "real_postgresql": True,
        "physical_android": True,
        "same_provider": False,
        "provider_independence_verified": True,
        "provider_independence_scope": "distinct_origin_asn",
        "full_upstream_independence_verified": False,
        "facts": {},
        "cleanup": {},
        "diagnostic_session": os.environ.get("VPN_I019_DIAGNOSTIC_SESSION") == "true",
    }
    progress_lock = threading.Lock()
    attempt_id = uuid4().hex
    progress_path = ROOT / "docs/evidence/i-019-progress.json"

    def progress(current_stage: str) -> None:
        # Only fixed stages/facts; no app responses, profile, session or exception text.
        with progress_lock:
            value = {
                "issue": "I-019",
                "attempt": attempt_id,
                "updated_at": datetime.now(UTC).isoformat(),
                "stage": current_stage,
                "facts": dict(report["facts"]),
                "phone_failure": report.get("phone_failure"),
                "live_phone": report.get("live_phone"),
                "egress_probes": list(report.get("egress_probes", [])),
                "agent_b": report.get("agent_b"),
                "api_failures": list(report.get("api_failures", [])),
            }
            temporary = progress_path.with_suffix(".next.json")
            temporary.write_text(json.dumps(value, indent=2) + "\n")
            temporary.replace(progress_path)

    profiles: dict[str, dict[str, Any]] = {}
    bootstraps: dict[str, dict[str, str]] = {}
    node_ids: dict[str, UUID] = {}
    tunnels: dict[str, subprocess.Popen[bytes] | None] = {host: None for host in HOSTS}
    touched: set[str] = set()
    approved_grants: set[str] = set()
    phone_touched = False
    probe_base_touched = False
    probe_test_touched = False
    reverse_touched = False
    probe_executor = ThreadPoolExecutor(max_workers=1)
    probe_jobs: dict[str, tuple[Any, str]] = {}
    a_tunnels: list[UUID] = []
    job: dict[str, Any] = {"thread": None, "done": False, "failed": False}
    job_cancel = threading.Event()
    app = create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender)
    app.add_middleware(I019ApiFailureObserver, report=report)
    stage = "preflight"
    progress(stage)
    actual_commands = lifecycle_commands
    actual_ack = lifecycle_apply_ack
    agent_events = AgentEventRecorder()

    def observe_commands(
        session: Session, node_id: UUID, inventory: list[str]
    ) -> LifecycleCommands:
        result = actual_commands(session, node_id, inventory)
        if node_id == node_ids.get(HOSTS[1]) and result.commands:
            state = report.setdefault("agent_b", {"deliveries": 0, "acks": 0})
            state["deliveries"] += 1
            command = result.commands[0]
            agent_events.record(
                "delivery", command.peer_id, command.operation, command.peer_version
            )
            state["events"], state["events_truncated"] = agent_events.snapshot()
            state["last_delivery"] = {
                "operation": command.operation,
                "version": command.peer_version,
                "budget_bytes": command.budget_bytes,
                "remaining_seconds": round(
                    (command.lease_deadline - datetime.now(UTC)).total_seconds(), 3
                ),
                "inventory_count": len(inventory),
            }
            progress("agent_command")
        return result

    def observe_ack(session: Session, node_id: UUID, body: LifecycleAck) -> str:
        if node_id != node_ids.get(HOSTS[1]):
            return actual_ack(session, node_id, body)
        state = report.setdefault("agent_b", {"deliveries": 0, "acks": 0})
        state["acks"] += 1
        state["last_ack"] = {
            "operation": body.operation,
            "applied": body.applied,
            "version": body.peer_version,
        }
        try:
            result = actual_ack(session, node_id, body)
            agent_events.record(
                "ack",
                body.peer_id,
                body.operation,
                body.peer_version,
                handler_returned=True,
                applied=body.applied,
            )
            state["events"], state["events_truncated"] = agent_events.snapshot()
            return result
        except NodeError as failure:
            agent_events.record(
                "ack",
                body.peer_id,
                body.operation,
                body.peer_version,
                handler_returned=False,
                applied=body.applied,
            )
            state["events"], state["events_truncated"] = agent_events.snapshot()
            state["ack_error"] = (
                failure.code
                if failure.code
                in {
                    "peer_settlement_conflict",
                    "peer_version_conflict",
                    "peer_state_conflict",
                    "peer_lease_expired",
                    "peer_budget_exceeded",
                    "peer_settlement_failed",
                    "not_found",
                }
                else "other"
            )
            raise
        finally:
            progress("agent_ack")

    # Observe real lifecycle calls only. No synthetic commands, ACKs or clock changes.
    monkeypatch.setattr(tunnel_routes, "commands", observe_commands)
    monkeypatch.setattr(tunnel_routes, "apply_ack", observe_ack)

    def write_manifest() -> None:
        temporary = PRIVATE / "nodes.next.json"
        temporary.write_text(json.dumps({"version": 1, "profiles": list(profiles.values())}))
        temporary.replace(manifest)  # Inherits owner-only directory ACL.

    def start_node(host: str, cancel: threading.Event | None = None) -> None:
        check_job_cancelled(cancel)
        if tunnels[host] is None:
            tunnels[host] = transport(host)
        body = bootstraps[host] | {"runtime_id": str(uuid4())}
        bootstraps[host] = body
        touched.add(host)  # Ambiguous SSH response still requires cleanup.
        remote(host, "prepare", json.dumps(body).encode())
        check_job_cancelled(cancel)
        remote(host, "start")
        check_job_cancelled(cancel)
        selected = remote(host, "profile")
        try:
            NodeProfile.model_validate(selected)
        except ValueError:
            raise RuntimeError("invalid_private_profile") from None
        profiles[host] = selected
        write_manifest()
        for _ in range(50):
            check_job_cancelled(cancel)
            with Session(h.engine) as session:
                node = session.get(VPNNode, node_ids[host])
                if (
                    node is not None
                    and str(node.peer_runtime_id) == body["runtime_id"]
                    and node.peer_protocol_version == 2
                ):
                    return
            time.sleep(0.25)
        report["agent_negotiation_diagnostics"] = remote(host, "diagnose")
        raise RuntimeError("real_agent_not_negotiated")

    def owner(request: Request) -> UUID:
        with Session(h.engine) as session, session.begin():
            principal = h.auth.authenticate(
                session,
                request.headers.get("Authorization", "").removeprefix("Bearer "),
                "tunnels:manage",
            )
            return principal.user_id

    def start_job(operation: str) -> dict[str, bool]:
        prior = job["thread"]
        assert prior is None or not prior.is_alive()
        job["done"], job["failed"] = False, False

        def work() -> None:
            try:
                check_job_cancelled(job_cancel)
                if operation == "stop":
                    assert remote(HOSTS[0], "stop").get("cleanup_complete") is True
                    report["node_a_runtime_stopped_before_expiry"] = True
                else:
                    state = remote(HOSTS[0], "status")
                    check_job_cancelled(job_cancel)
                    if state["endpoint_active"]:
                        assert remote(HOSTS[0], "stop").get("cleanup_complete") is True
                    check_job_cancelled(job_cancel)
                    start_node(HOSTS[0], job_cancel)
                    check_job_cancelled(job_cancel)
                    assert remote(HOSTS[0], "gate").get("kernel_gate_empty") is True
                    with Session(h.engine) as session:
                        for tunnel_id in a_tunnels:
                            tunnel = session.get(Tunnel, tunnel_id)
                            assert tunnel is not None and tunnel.state == "revoked"
                job["done"] = True
            except Exception as failure:
                # No remote output or exception data is exposed by test coordination.
                if isinstance(failure, RemoteActionFailure):
                    report["remote_failure"] = failure.facts
                job["failed"] = True

        job["thread"] = threading.Thread(target=work, daemon=True)
        job["thread"].start()
        return {"started": True}

    @app.post("/api/v1/__i019__/job", include_in_schema=False)
    def test_job(request: Request) -> dict[str, bool]:
        owner(request)
        return {"done": job["done"], "failed": job["failed"]}

    @app.post("/api/v1/__i019__/issue", include_in_schema=False)
    def test_issue() -> dict[str, str]:
        with Session(h.engine) as session:
            attempt = session.scalar(select(LoginAttempt).order_by(LoginAttempt.created_at.desc()))
            assert attempt is not None
            challenge = StartResponse(
                request_id=attempt.id,
                verifier="TEST-ONLY-UNUSED",
                bot_url="https://t.me/LocalTestLoginBot",
                expires_at=attempt.expires_at,
            )
        return {"code": issue(h, challenge, subject=2**40 + 1900)}

    @app.post("/api/v1/__i019__/nodes", include_in_schema=False)
    def test_nodes(request: Request) -> dict[str, Any]:
        owner(request)
        return {
            "a": str(node_ids[HOSTS[0]]),
            "b": str(node_ids[HOSTS[1]]),
            "diagnostic_b_reuse": b_only,
        }

    @app.post("/api/v1/__i019__/active", include_in_schema=False)
    def test_active(request: Request) -> dict[str, Any]:
        user_id = owner(request)
        with Session(h.engine) as session:
            active = session.scalar(
                select(Tunnel).where(Tunnel.user_id == user_id, Tunnel.state == "active")
            )
            assert active is not None and active.lease_deadline is not None
            if active.node_id == node_ids[HOSTS[0]] and active.id not in a_tunnels:
                a_tunnels.append(active.id)
            return {
                "id": str(active.id),
                "device_id": str(active.device_id),
                "deadline": active.lease_deadline.isoformat(),
            }

    @app.post("/api/v1/__i019__/freeze-a", include_in_schema=False)
    def test_freeze(request: Request) -> dict[str, bool]:
        owner(request)
        assert remote(HOSTS[0], "freeze-agent").get("agent_frozen") is True
        terminate(tunnels[HOSTS[0]])
        tunnels[HOSTS[0]] = None
        report["agent_frozen_and_api_transport_removed"] = True
        return {"frozen": True}

    @app.post("/api/v1/__i019__/kernel-expired", include_in_schema=False)
    def test_kernel_expired(request: Request) -> dict[str, bool]:
        owner(request)
        assert tunnels[HOSTS[0]] is None
        with Session(h.engine) as session:
            tunnel = session.get(Tunnel, a_tunnels[0])
            assert tunnel is not None and tunnel.lease_deadline is not None
            assert datetime.now(UTC) > tunnel.lease_deadline
        assert remote(HOSTS[0], "gate").get("kernel_gate_empty") is True
        report["kernel_gate_empty_with_agent_frozen_and_api_absent"] = True
        return {"expired": True}

    @app.post("/api/v1/__i019__/stop-a", include_in_schema=False)
    def test_stop_a(request: Request) -> dict[str, bool]:
        user_id = owner(request)
        with Session(h.engine) as session:
            tunnel = session.scalar(
                select(Tunnel).where(Tunnel.user_id == user_id, Tunnel.state == "active")
            )
            assert tunnel is not None and tunnel.lease_deadline is not None
            assert (tunnel.lease_deadline - datetime.now(UTC)).total_seconds() > 15
        return start_job("stop")

    @app.post("/api/v1/__i019__/return-a", include_in_schema=False)
    def test_return(request: Request) -> dict[str, bool]:
        owner(request)
        return start_job("return")

    @app.post("/api/v1/__i019__/fact", include_in_schema=False)
    def test_fact(request: Request, value: dict[str, str]) -> dict[str, bool]:
        owner(request)
        name = value.get("name")
        assert name in FACTS
        report["facts"][name] = True
        progress(name)
        return {"accepted": True}

    @app.post("/api/v1/__i019__/progress", include_in_schema=False)
    def test_progress(request: Request, value: dict[str, Any]) -> dict[str, bool]:
        owner(request)
        current = value.get("stage", "")
        assert isinstance(current, str) and current in FAILURE_STAGES
        report["live_phone"] = sanitized_phone_failure(value)
        progress(current)
        return {"recorded": True}

    @app.post("/api/v1/__i019__/probe", include_in_schema=False)
    def test_probe(request: Request, value: dict[str, Any]) -> dict[str, bool]:
        owner(request)
        probes = report.setdefault("egress_probes", [])
        append_egress_probe(probes, value)
        progress("egress_probe")
        return {"recorded": True}

    @app.post("/api/v1/__i019__/egress-probe", include_in_schema=False)
    def test_independent_egress_probe(request: Request, value: dict[str, Any]) -> dict[str, str]:
        owner(request)
        probe_stage = value.get("stage")
        assert isinstance(probe_stage, str) and probe_stage in FAILURE_STAGES
        assert len(probe_jobs) < 2
        probe_id = uuid4().hex

        def perform_probe() -> tuple[str, str]:
            try:
                output = adb(
                    "shell",
                    "am",
                    "instrument",
                    "-w",
                    "-r",
                    "-e",
                    "mode",
                    "i019",
                    f"{PROBE_TEST_PACKAGE}/org.example.vpnpoc.I028Probe",
                    timeout=25,
                )
                outcome = independent_egress_result(output)
                trigger = independent_trigger_result(output)
                del output
                return outcome, trigger
            except (RuntimeError, subprocess.SubprocessError):
                return "adb_error", "unavailable"

        probe_jobs[probe_id] = (probe_executor.submit(perform_probe), probe_stage)
        return {"probe_id": probe_id}

    @app.post("/api/v1/__i019__/egress-probe-result", include_in_schema=False)
    def test_independent_egress_probe_result(
        request: Request, value: dict[str, Any]
    ) -> dict[str, Any]:
        owner(request)
        probe_id = value.get("probe_id")
        assert isinstance(probe_id, str) and re.fullmatch(r"[0-9a-f]{32}", probe_id)
        job_value = probe_jobs.get(probe_id)
        assert job_value is not None
        future, probe_stage = job_value
        if not future.done():
            return {"done": False}
        try:
            outcome, trigger = future.result()
        except Exception:
            outcome, trigger = "probe_task_error", "unavailable"
        probe_jobs.pop(probe_id, None)
        probes = report.setdefault("egress_probes", [])
        sanitized = append_egress_probe(
            probes,
            {"stage": probe_stage, "phase": "response", "result": outcome, "trigger": trigger},
        )
        progress("egress_probe")
        return {"done": True, "result": sanitized["result"]}

    @app.post("/api/v1/__i019__/normal-network", include_in_schema=False)
    def test_normal_network(request: Request) -> dict[str, bool]:
        owner(request)
        raw = adb("shell", "dumpsys", "connectivity").decode(errors="replace")
        state = normal_android_network(raw)
        del raw
        report["last_default_network"] = state
        return state

    @app.post("/api/v1/__i019__/failure", include_in_schema=False)
    def test_failure(request: Request, value: dict[str, Any]) -> dict[str, bool]:
        user_id = owner(request)
        report["phone_failure"] = sanitized_phone_failure(value)
        progress("phone_failure")
        with Session(h.engine) as session:
            snapshot = {}
            for label, host in zip(("a", "b"), HOSTS, strict=True):
                node = session.get(VPNNode, node_ids[host])
                assert node is not None
                rows = list(
                    session.scalars(
                        select(Tunnel).where(
                            Tunnel.user_id == user_id,
                            Tunnel.node_id == node.id,
                        )
                    )
                )
                commands = list(
                    session.scalars(
                        select(PeerCommand).where(
                            PeerCommand.node_id == node.id,
                        )
                    )
                )
                snapshot[label] = {
                    "heartbeat_age_seconds": None
                    if node.last_seen_at is None
                    else round((datetime.now(UTC) - node.last_seen_at).total_seconds(), 3),
                    "runtime_matches": str(node.peer_runtime_id) == bootstraps[host]["runtime_id"],
                    "tunnel_states": [row.state for row in rows],
                    "commands": [
                        {"operation": c.operation, "acknowledged": c.acknowledged_at is not None}
                        for c in commands
                    ],
                }
            report["failure_control_plane"] = snapshot
        # Diagnose possible byte-gate exhaustion through the real public revoke/ACK
        # path. Capture aggregate counters only; never packets, keys or auth data.
        with Session(h.engine) as session:
            active = session.scalar(
                select(Tunnel).where(
                    Tunnel.user_id == user_id,
                    Tunnel.state == "active",
                )
            )
            failed_tunnel_id = None if active is None else active.id
        if failed_tunnel_id is not None:
            try:
                # Direct loopback connection ignores environment/system HTTP proxies.
                connection = http.client.HTTPConnection("127.0.0.1", 18766, timeout=2)
                try:
                    connection.request(
                        "DELETE",
                        "/api/v1/tunnels/" + str(failed_tunnel_id),
                        headers={"Authorization": request.headers["Authorization"]},
                    )
                    response = connection.getresponse()
                    response.read(8192)
                    if response.status != 200:
                        raise ValueError("i019_diagnostic_revoke_failed")
                finally:
                    connection.close()
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    with Session(h.engine) as session:
                        reservation = session.scalar(
                            select(TunnelReservation).where(
                                TunnelReservation.tunnel_id == failed_tunnel_id,
                                TunnelReservation.user_id == user_id,
                            )
                        )
                        if reservation is not None and reservation.settled_at is not None:
                            report["failure_settlement"] = {
                                "budget_bytes": reservation.amount_bytes,
                                "observed_bytes": reservation.observed_bytes,
                                "charged_bytes": reservation.charged_bytes,
                            }
                            break
                    time.sleep(0.2)
            except (OSError, http.client.HTTPException, ValueError):
                report["failure_settlement"] = {"unavailable": True}
        return {"recorded": True}

    @app.post("/api/v1/__i019__/verify", include_in_schema=False)
    def test_verify(request: Request) -> dict[str, bool]:
        user_id = owner(request)
        with Session(h.engine) as session:
            reservations = list(
                session.scalars(
                    select(TunnelReservation).where(TunnelReservation.user_id == user_id)
                )
            )
            assert len(reservations) == (2 if b_only else 4)
            assert len(a_tunnels) == (0 if b_only else 2)
            for reservation in reservations:
                assert reservation.settled_at is not None and reservation.charged_bytes is not None
                if reservation.tunnel_id in a_tunnels:
                    assert reservation.observed_bytes is None
                    assert reservation.charged_bytes == reservation.amount_bytes == 16777216
                else:
                    assert reservation.observed_bytes is not None
                rows = list(
                    session.scalars(
                        select(QuotaLedger).where(
                            QuotaLedger.source_id == reservation.tunnel_id,
                            QuotaLedger.source_type == "tunnel_lease",
                        )
                    )
                )
                assert len(rows) == 1 and rows[0].delta_bytes == -reservation.charged_bytes
            quota = LedgerService(h.engine).quota(session, user_id)
            assert quota.reserved_bytes == 0
            assert quota.available_bytes == 67108864 - sum(
                r.charged_bytes or 0 for r in reservations
            )
            report["ledger"] = {
                "settlements": len(reservations),
                "lost_counter_full_debits": len(a_tunnels),
                "reserved_bytes": 0,
                "available_bytes": quota.available_bytes,
            }
        return {"ledger_consistent": True}

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=18766,
            access_log=False,
            log_config=None,
            log_level="critical",
            timeout_graceful_shutdown=5,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    try:
        for host in HOSTS:
            if host in active_hosts:
                state = remote(host, "status")
                assert state == {"endpoint_active": False, "agent_active": False}
                approved_grants.add(host)
            node = NodeRegistry(h.engine).provision(
                ProvisionNodeInput(
                    idempotency_key=uuid4(),
                    name="I019 staging " + ("A" if host == HOSTS[0] else "B"),
                    region="staging",
                )
            )
            if node.secret is None:
                raise RuntimeError("node_credential_missing")
            node_ids[host] = node.node_id
            bootstraps[host] = {
                "node_id": str(node.node_id),
                "runtime_id": str(uuid4()),
                "credential": node.secret,
                "bootstrap_public_key": base64.b64encode(
                    X25519PrivateKey.generate().public_key().public_bytes_raw()
                ).decode(),
            }
        probe_base = Path(os.environ["VPN_I019_PROBE_BASE_APK"])
        probe_test = Path(os.environ["VPN_I019_PROBE_TEST_APK"])
        for path, variable in (
            (probe_base, "VPN_I019_PROBE_BASE_SHA256"),
            (probe_test, "VPN_I019_PROBE_TEST_SHA256"),
        ):
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == os.environ[variable]
        installed = adb("shell", "pm", "list", "packages").decode("ascii", "ignore")
        if any(
            line.strip() in {f"package:{PROBE_PACKAGE}", f"package:{PROBE_TEST_PACKAGE}"}
            for line in installed.splitlines()
        ):
            raise RuntimeError("independent_probe_package_already_installed")
        adb("install", str(probe_base), timeout=90)
        probe_base_touched = True
        adb("install", str(probe_test), timeout=90)
        probe_test_touched = True
        report["clock_offset_upper_bounds_seconds"] = {}
        for host in active_hosts:
            try:
                report["clock_offset_upper_bounds_seconds"][host] = clock_bound(host)
            except ClockBoundExceeded as failure:
                report["clock_offset_upper_bounds_seconds"][host] = failure.upper_bound_seconds
                raise
        thread.start()
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        stage = "start_two_nodes"
        progress(stage)
        for host in active_hosts:
            start_node(host)
        reverse_touched = True
        adb("reverse", "tcp:18766", "tcp:18766")
        adb(
            "install",
            "-r",
            str(ROOT / "apps/client/build/app/outputs/flutter-apk/app-debug.apk"),
            timeout=90,
        )
        phone_touched = True
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "allow")
        client = Path(os.environ["VPN_I019_CLIENT_DIR"])
        sdk = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/toolchains/flutter"
        env = dict(os.environ)
        env["JAVA_HOME"] = r"C:\Program Files\Android\Android Studio\jbr"
        env["PUB_CACHE"] = str(sdk.parent / "pub-cache")
        serial = [
            line.split()[0]
            for line in adb("devices").decode().splitlines()
            if line.endswith("\tdevice")
        ][0]
        stage = "physical_failover"
        progress(stage)
        result = subprocess.run(  # noqa: S603 - fixed physical test, secrets never in CLI
            [
                str(sdk / "bin/flutter.bat"),
                "test",
                "integration_test/node_failover_live_test.dart",
                "-d",
                serial,
                "--no-pub",
            ],
            cwd=client,
            env=env,
            capture_output=True,
            timeout=900,
            check=False,
        )
        success = result.returncode == 0
        # Raw Flutter output might contain API data: retain only a fixed stage label.
        match = re.search(rb"i019_stage_([a-z_]{1,50})", result.stdout + result.stderr)
        if match is not None:
            report["phone_failure_stage"] = match[1].decode()
        del result
        assert success
        assert all(report["facts"].get(name) is True for name in required_facts)
        assert report.get("ledger") is not None
        report["passed"] = True
        stage = "cleanup"
    finally:
        active_failure = sys.exc_info()[1]
        if isinstance(active_failure, RemoteActionFailure):
            report["remote_failure"] = active_failure.facts
        cancel_job_and_wait(job_cancel, job["thread"])
        if stage == "physical_failover" and not report["passed"]:
            report["failure_nodes"] = {}
            for host in touched:
                try:
                    report["failure_nodes"][host] = {
                        "status": remote(host, "status"),
                        "diagnostics": remote(host, "diagnose"),
                        "gate": remote(host, "gate"),
                    }
                except (RuntimeError, ValueError, subprocess.SubprocessError):
                    report["failure_nodes"][host] = {"unavailable": True}
        retain_attempt_grants = retain_session_grants(
            session=report["diagnostic_session"],
            passed=report["passed"],
            diagnostic_only=b_only,
        )
        for host in HOSTS:
            if host in touched or host in approved_grants:
                try:
                    result_cleanup = finish_node_attempt(
                        host,
                        retain_grant=retain_attempt_grants,
                    )
                    report["cleanup"][host] = result_cleanup
                    report["passed"] &= result_cleanup.get("cleanup_complete") is True and (
                        result_cleanup.get("control_grant_removed") is True
                        or (
                            b_only
                            and retain_attempt_grants
                            and result_cleanup.get("control_grant_retained_until_original_deadline")
                            is True
                        )
                    )
                except CleanupNotConfirmed as failure:
                    report["passed"] = False
                    report["cleanup"][host] = failure.receipt()
                except RemoteActionFailure:
                    report["passed"] = False
                    report["cleanup"][host] = {
                        "error": "cleanup_not_confirmed",
                        "phase": "remote_action",
                    }
                except (RuntimeError, ValueError, subprocess.SubprocessError):
                    report["passed"] = False
                    report["cleanup"][host] = {"error": "cleanup_not_confirmed"}
            terminate(tunnels[host])
        server.should_exit = True
        probe_executor.shutdown(wait=True, cancel_futures=True)
        if thread.is_alive():
            thread.join(timeout=15)
        phone_clean = True
        try:
            if phone_touched and ("package:" + PACKAGE).encode() in adb(
                "shell", "pm", "list", "packages", PACKAGE
            ):
                adb("shell", "am", "force-stop", PACKAGE)
                adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "default")
        except (RuntimeError, subprocess.SubprocessError):
            # Flutter may uninstall between pm-list and appops. Absence is clean.
            try:
                phone_clean = ("package:" + PACKAGE).encode() not in adb(
                    "shell", "pm", "list", "packages", PACKAGE
                )
            except (RuntimeError, subprocess.SubprocessError):
                phone_clean = False
        try:
            if reverse_touched and b"tcp:18766" in adb("reverse", "--list"):
                adb("reverse", "--remove", "tcp:18766")
            phone_clean &= not reverse_touched or b"tcp:18766" not in adb("reverse", "--list")
        except (RuntimeError, subprocess.SubprocessError):
            phone_clean = False
        probe_clean = True
        for package, was_touched in (
            (PROBE_TEST_PACKAGE, probe_test_touched),
            (PROBE_PACKAGE, probe_base_touched),
        ):
            if was_touched:
                try:
                    adb("uninstall", package)
                except (RuntimeError, subprocess.SubprocessError):
                    probe_clean = False
        try:
            installed_after = adb("shell", "pm", "list", "packages").decode("ascii", "ignore")
            probe_clean &= all(
                f"package:{package}" not in installed_after.splitlines()
                for package in (PROBE_PACKAGE, PROBE_TEST_PACKAGE)
            )
        except (RuntimeError, subprocess.SubprocessError):
            probe_clean = False
        report["independent_probe_removed"] = probe_clean
        phone_clean &= probe_clean
        report["phone_stopped_and_reverse_removed"] = phone_clean
        manifest.unlink(missing_ok=True)
        (PRIVATE / "nodes.next.json").unlink(missing_ok=True)
        report["passed"] &= not thread.is_alive() and report["phone_stopped_and_reverse_removed"]
        if not report["passed"]:
            report["failed_at"] = stage
        receipt_name = "i-019-b-reuse-diagnostic.json" if b_only else "i-019-staging.json"
        (ROOT / "docs/evidence" / receipt_name).write_text(json.dumps(report, indent=2) + "\n")
        (ROOT / f"docs/evidence/i-019-attempt-{attempt_id}.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        runtime_clean = report["phone_stopped_and_reverse_removed"] and all(
            report["cleanup"].get(host, {}).get("cleanup_complete") is True for host in active_hosts
        )
        progress(
            "completed"
            if report["passed"]
            else "failed_runtime_stopped"
            if runtime_clean
            else "failed_cleanup_not_confirmed"
        )
    assert report["passed"]

"""Opt-in I-028: real phone/AWG/staging A, disposable local API/PG; fake Telegram.

Never print raw Flutter, instrumentation, SSH manifest or credential diagnostics.
"""

import asyncio
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import uvicorn
from fastapi import Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from test_authentication import Harness, harness, issue
from test_quota import event

from vpn_api.auth.contracts import StartResponse
from vpn_api.auth.security import Principal
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    LoginAttempt,
    PeerConnection,
    PeerRuntimeEpoch,
    PeerWindowHold,
    PeerWindowReceipt,
    SessionFamily,
    VPNNode,
)
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeRegistry
from vpn_api.quota.service import LedgerService

__all__ = ["harness"]
ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "org.example.vpnclient.i028"
TEST_PACKAGE = f"{PACKAGE}.test"
SSH = [
    "wsl.exe",
    "-d",
    "kali-linux",
    "--",
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=yes",
    "-i",
    "/home/ad/.ssh/vpn_staging_20261019",
]
HOST = "vpn-deploy@45.10.166.106"
HELPER = "sudo -n /usr/local/libexec/detourvpn-i028-control "
FACTS = {
    "real_account_refresh_after_android_process_restart",
    "included_probe_real_staging_ipv4_and_udp_dns",
    "sustained_real_renewals_over_three_minutes",
    "disconnect_native_down_and_server_closed",
    "same_probe_uid_included_then_excluded_egress",
    "remote_revoke_stops_actual_android_tunnel",
    "api_agent_loss_native_and_kernel_deadlines_close",
    "agent_restart_reconciles_before_new_real_tunnel",
    "disconnect_during_real_renewal_response_cannot_reopen",
}
LIFECYCLE_FACTS = {
    "real_account_refresh_after_android_process_restart",
    "background_window_safe_and_real_recovery",
    "screen_off_window_safe_and_real_recovery",
    "forced_doze_window_safe_and_real_recovery",
    "wifi_mobile_loss_wifi_real_transfer_and_valid_windows",
    "system_settings_revoke_closes_without_self_restart",
}
NETWORK_REVOKE_FACTS = {
    "real_account_refresh_after_android_process_restart",
    "wifi_mobile_loss_wifi_real_transfer_and_valid_windows",
    "system_settings_revoke_closes_without_self_restart",
}
NATURAL_SLEEP_FACTS = {
    "real_account_refresh_after_android_process_restart",
    "natural_sleep_native_down_after_wake",
    "natural_sleep_new_staging_egress_after_recovery",
    "natural_sleep_window_cutoff_and_real_recovery",
}
EGRESS_DIAGNOSTIC_FACTS = {
    "real_account_refresh_after_android_process_restart",
    "included_probe_real_staging_ipv4_and_udp_dns",
    "included_ipv6_literal_blocked",
}


def i028_suite_config(suite: str) -> tuple[bool, str, set[str]]:
    configs = {
        "Main": (False, "run", FACTS),
        "Egress": (False, "egress", EGRESS_DIAGNOSTIC_FACTS),
        "Lifecycle": (True, "lifecycle", LIFECYCLE_FACTS),
        "NetworkRevoke": (True, "network_revoke", NETWORK_REVOKE_FACTS),
        "NaturalSleep": (True, "natural_sleep", NATURAL_SLEEP_FACTS),
    }
    if suite not in configs:
        raise ValueError("i028_suite_invalid")
    return configs[suite]


PROBE_FLAGS = {
    "active_transport_cellular",
    "active_transport_wifi",
    "application_initialized",
    "process_uid_matches_target",
    "test_internet_permission_declared",
    "ipv4_available",
    "ipv4_used_fallback",
    "ipv6_available",
    "ipv6_literal_connected",
    "ipv6_literal_connect_attempted",
    "ipv6_literal_egress_port_control_connected",
    "egress_is_staging",
    "fixed_udp_dns_response",
    "active_network_is_vpn",
    "vpn_dns_configured",
    "active_network_available",
    "active_network_validated",
    "network_metadata_unavailable",
    "probe_complete",
}
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("VPN_I028_STAGING_APPROVED") != "45.10.166.106",
        reason="bounded I-028 staging not requested",
    ),
]


def command(parts: list[str], *, data: bytes | None = None, timeout: int = 60) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed tools and reviewed argument lists.
        parts,
        input=data,
        capture_output=True,  # noqa: S603
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("i028_fixed_tool_failed")
    return result.stdout


def helper(verb: str, value: dict[str, Any] | None = None, *, timeout: int = 60) -> dict[str, Any]:
    assert verb in {
        "status",
        "manifest",
        "configure",
        "start-agent",
        "stop-agent",
        "crash-agent",
        "stop",
    }
    raw = command(
        [*SSH, HOST, HELPER + verb],
        data=json.dumps(value).encode() if value is not None else None,
        timeout=timeout,
    )
    result = json.loads(raw)
    assert isinstance(result, dict)
    return result


def parse_probe(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"INSTRUMENTATION_RESULT: ([a-z0-9_]+)=(.+)", line)
        if match is None:
            continue
        name, value = match.groups()
        if name in PROBE_FLAGS and value in ("true", "false"):
            result[name] = value == "true"
        elif name in {
            "ipv4_failure",
            "ipv4_primary_failure",
            "ipv6_failure",
            "ipv6_literal_failure",
            "ipv6_literal_egress_port_control_failure",
            "dns_failure",
        } and re.fullmatch(
            r"none|dns|tls|timeout|connect|permission|io|route|http_[1-5][0-9]{2}", value
        ):
            result[name] = value
        elif name in {
            "fixed_resolver_result",
            "fixed_tcp_result",
            "fixed_tls_result",
        } and value in {
            "ok",
            "timeout",
            "dns",
            "tls",
            "connect",
            "permission",
            "io",
            "route",
            "not_attempted",
            "interrupted",
        }:
            result[name] = value
    if result.get("probe_complete") is not True:
        raise RuntimeError("i028_probe_incomplete")
    return result


def sanitize_recovery_node_status(value: object) -> dict[str, Any]:
    """Retain only bounded aggregate staging-slot observations, never peer identities."""
    if not isinstance(value, dict):
        return {"available": False}
    peers = value.get("peer_counts")
    live = value.get("live_slots")
    transfer = value.get("native_transfer_bytes")
    if (
        value.get("prepared") is not True
        or type(value.get("agent_active")) is not bool
        or not isinstance(peers, list)
        or len(peers) != 2
        or any(type(x) is not int or not 0 <= x <= 2 for x in peers)
        or not isinstance(live, list)
        or len(live) != len(set(x for x in live if type(x) is int and x in (1, 2)))
        or not isinstance(transfer, list)
        or len(transfer) != 2
    ):
        return {"available": False}
    counters = []
    for slot, row in enumerate(transfer, 1):
        if not isinstance(row, dict) or type(row.get("slot")) is not int or row["slot"] != slot:
            return {"available": False}
        received, sent = row.get("received"), row.get("sent")
        if not all(
            isinstance(x, str) and re.fullmatch(r"[0-9]{1,20}", x) for x in (received, sent)
        ):
            return {"available": False}
        counters.append({"slot": slot, "received": received, "sent": sent})
    result = {
        "available": True,
        "agent_active": value["agent_active"],
        "live_slots": live,
        "peer_counts": peers,
        "native_transfer_bytes": counters,
    }
    quota = value.get("byte_quota_exhausted")
    if (
        isinstance(quota, list)
        and len(quota) == len(live)
        and all(
            isinstance(row, dict)
            and type(row.get("slot")) is int
            and row["slot"] in live
            and (row.get("exhausted") is None or type(row.get("exhausted")) is bool)
            and "exhausted" in row
            for row in quota
        )
        and len({row["slot"] for row in quota}) == len(live)
    ):
        result["byte_quota_exhausted"] = []
        for row in quota:
            clean_row = {"slot": row["slot"], "exhausted": row["exhausted"]}
            remaining = row.get("remaining_bytes")
            if type(remaining) is int and 0 <= remaining <= 67108864:
                clean_row["remaining_bytes"] = remaining
            result["byte_quota_exhausted"].append(clean_row)
    return result


def sanitize_recovery_connection_status(value: object) -> dict[str, Any]:
    """Keep bounded grant state only; never retain row identifiers."""
    if not isinstance(value, dict) or value.get("available") is not True:
        return {"available": False}
    state = value.get("state")
    generation = value.get("generation")
    budget = value.get("budget_bytes")
    remaining = value.get("lease_remaining_seconds")
    if (
        not isinstance(state, str)
        or state not in {"active", "pending", "revoke_pending", "closed", "absent"}
        or type(generation) is not int
        or not 0 <= generation <= 1000000
        or type(budget) is not int
        or not 0 <= budget <= 67108864
        or type(remaining) is not int
        or not 0 <= remaining <= 90
    ):
        return {"available": False}
    return {
        "available": True,
        "state": state,
        "generation": generation,
        "budget_bytes": budget,
        "lease_remaining_seconds": remaining,
    }


def sanitize_egress_renewal_status(value: object) -> dict[str, Any]:
    """Expose only bounded generation/state needed to await an Egress ACK."""
    if not isinstance(value, dict) or value.get("available") is not True:
        return {"available": False}
    state = value.get("state")
    generation = value.get("generation")
    if (
        not isinstance(state, str)
        or state not in {"active", "pending", "revoke_pending", "closed", "absent"}
        or type(generation) is not int
        or not 0 <= generation <= 1000000
    ):
        return {"available": False}
    renewal_seen = generation >= 2
    return {
        "available": True,
        "renewal_seen": renewal_seen,
        "acknowledged": renewal_seen and state == "active",
        "generation": generation,
        "state": state,
    }


def sanitize_egress_hold_observations(value: object) -> dict[str, Any]:
    """Retain bounded phase, handshake-age, lease, and RX-progress aggregates."""
    if not isinstance(value, dict) or set(value) != {
        "observations",
        "fixed_probe_attempted",
        "fixed_probe_succeeded",
    }:
        raise ValueError("egress_hold_observation_invalid")
    if (
        type(value["fixed_probe_attempted"]) is not bool
        or type(value["fixed_probe_succeeded"]) is not bool
        or (value["fixed_probe_succeeded"] and not value["fixed_probe_attempted"])
    ):
        raise ValueError("egress_hold_observation_invalid")
    observations = value["observations"]
    if not isinstance(observations, list) or not 1 <= len(observations) <= 32:
        raise ValueError("egress_hold_observation_invalid")

    allowed_phases = {"connected", "reconnecting", "failed", "connecting", "disconnected"}
    clean: list[dict[str, Any]] = []
    previous_elapsed = -1
    for item in observations:
        fields = {
            "elapsed_seconds",
            "client_phase",
            "client_has_error",
            "native_up",
            "native_network",
            "handshake_age",
            "remaining_ms",
            "received_counter_available",
            "inbound_progress_recent",
            "renewal_acknowledged",
            "traffic_counter_available",
            "traffic_renewal_requested",
            "renewal_pending",
        }
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("egress_hold_observation_invalid")
        elapsed = item["elapsed_seconds"]
        phase = item["client_phase"]
        handshake_age = item["handshake_age"]
        remaining = item["remaining_ms"]
        counter_available = item["received_counter_available"]
        inbound_recent = item["inbound_progress_recent"]
        if (
            type(elapsed) is not int
            or not 0 <= elapsed <= 300
            or elapsed <= previous_elapsed
            or not isinstance(phase, str)
            or phase not in allowed_phases
            or type(handshake_age) is not int
            or not -1 <= handshake_age <= 86400
            or type(remaining) is not int
            or not 0 <= remaining <= 90000
            or type(counter_available) is not bool
            or type(inbound_recent) is not bool
            or (inbound_recent and not counter_available)
            or any(
                type(item[field]) is not bool
                for field in (
                    "client_has_error",
                    "native_up",
                    "native_network",
                    "renewal_acknowledged",
                    "traffic_counter_available",
                    "traffic_renewal_requested",
                    "renewal_pending",
                )
            )
        ):
            raise ValueError("egress_hold_observation_invalid")
        previous_elapsed = elapsed
        clean.append(
            {
                "elapsed_seconds": elapsed,
                "client_phase": phase,
                "client_has_error": item["client_has_error"],
                "native_up": item["native_up"],
                "native_network": item["native_network"],
                "handshake_age": handshake_age,
                "remaining_ms": remaining,
                "received_counter_available": counter_available,
                "inbound_progress_recent": inbound_recent,
                "renewal_acknowledged": item["renewal_acknowledged"],
                "traffic_counter_available": item["traffic_counter_available"],
                "traffic_renewal_requested": item["traffic_renewal_requested"],
                "renewal_pending": item["renewal_pending"],
            }
        )

    fallback_observed = any(
        item["handshake_age"] > 60
        and item["inbound_progress_recent"]
        and item["client_phase"] == "connected"
        and not item["client_has_error"]
        and item["native_up"]
        and item["native_network"]
        and item["remaining_ms"] > 0
        for item in clean
    )
    return {
        "fixed_probe_attempted": value["fixed_probe_attempted"],
        "fixed_probe_succeeded": value["fixed_probe_succeeded"],
        "sample_count": len(clean),
        "stale_handshake_threshold_seconds": 60,
        "inbound_freshness_window_seconds": 60,
        "stale_handshake_connected_with_recent_inbound": fallback_observed,
        "observations": clean,
    }


def recovery_connection_snapshot(session: Session, user_id: Any) -> dict[str, Any]:
    """Return sanitized current grant metadata for test diagnostics."""
    try:
        connection = session.scalar(
            select(PeerConnection)
            .where(PeerConnection.user_id == user_id)
            .order_by(PeerConnection.created_at.desc())
            .limit(1)
        )
        if connection is None:
            return sanitize_recovery_connection_status(
                {
                    "available": True,
                    "state": "absent",
                    "generation": 0,
                    "budget_bytes": 0,
                    "lease_remaining_seconds": 0,
                }
            )
        hold = session.get(PeerWindowHold, connection.window_id)
        if hold is None:
            return {"available": False}
        remaining = max(
            0,
            min(90, int((hold.not_after - datetime.now(UTC)).total_seconds())),
        )
        return sanitize_recovery_connection_status(
            {
                "available": True,
                "state": connection.state,
                "generation": connection.generation,
                "budget_bytes": hold.amount_bytes,
                "lease_remaining_seconds": remaining,
            }
        )
    except Exception:
        # Diagnostic collection must never alter the physical acceptance result.
        return {"available": False}


def append_sustained_window_observation(
    report: dict[str, Any], native: object, connection: object
) -> None:
    """Retain at most 48 sanitized native and grant snapshots for Main diagnostics."""
    if not isinstance(native, dict):
        return
    up, network, remaining = (native.get("up"), native.get("network"), native.get("remaining_ms"))
    if (
        type(up) is not bool
        or type(network) is not bool
        or type(remaining) is not int
        or not 0 <= remaining <= 90000
    ):
        return
    observations = report.setdefault("sustained_window_observations", [])
    if not isinstance(observations, list) or len(observations) >= 48:
        return
    observations.append(
        {
            "native": {"up": up, "network": network, "remaining_ms": remaining},
            "connection": sanitize_recovery_connection_status(connection),
        }
    )


def sanitize_dns_observation(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("complete") is not True:
        raise ValueError("dns_observation_invalid")
    if value.get("packet_loss") is not False or not isinstance(value.get("counts"), dict):
        raise ValueError("dns_observation_invalid")
    counts = value["counts"]
    if set(counts) != {"tunnel", "uplink"}:
        raise ValueError("dns_observation_invalid")
    clean = {}
    for point in ("tunnel", "uplink"):
        item = counts[point]
        if not isinstance(item, dict) or set(item) != {"inbound", "outbound"}:
            raise ValueError("dns_observation_invalid")
        if any(type(item[x]) is not int or not 0 <= item[x] < 64 for x in item):
            raise ValueError("dns_observation_invalid")
        clean[point] = {"inbound": item["inbound"], "outbound": item["outbound"]}
    return {"complete": True, "packet_loss": False, "counts": clean}


DNS_OBSERVER_REMOTE_CATEGORIES = frozenset(
    {
        "single_live_slot_required",
        "insufficient_observation_window",
        "capture_not_ready",
        "capture_ended_early",
        "capture_stream_closed",
        "capture_stream_unavailable",
        "capture_linktype_unsupported",
        "capture_linktype_unavailable",
        "capture_sync_timeout",
        "capture_sync_invalid",
        "capture_packet_limit",
        "capture_output_limit",
        "packet_header_invalid",
        "packet_filter_mismatch",
        "capture_completion_unknown",
        "capture_incomplete",
        "capture_unavailable",
        "capture_tool_missing",
        "staging_deadline_expired",
        "observer_busy",
        "other",
    }
)


def dns_observer_remote_category(value: object) -> str:
    if not isinstance(value, dict) or value.get("error") != "dns_observe_failed":
        return "invalid_remote_result"
    category = value.get("category")
    if not isinstance(category, str) or category not in DNS_OBSERVER_REMOTE_CATEGORIES:
        return "invalid_remote_result"
    return category


def collect_dns_path_observation(
    command: list[str],
    run_probe: Callable[[], dict[str, Any]],
    *,
    ready_timeout: float = 30,
    finish_timeout: float = 20,
) -> dict[str, Any]:
    """Test-only fixed observer protocol; retain only allowlisted failures and counters."""
    stage = "launch"
    process: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    output_reader: threading.Thread | None = None
    failure = "launch_failed"
    phase_started = time.monotonic()
    timings: dict[str, int] = {}

    def elapsed() -> int:
        # Rounded, bounded duration only; no timestamps or user activity.
        return min(120000, max(0, int((time.monotonic() - phase_started) * 10) * 100))

    try:
        process = subprocess.Popen(  # noqa: S603 - caller passes the fixed test operator only
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        failure = "unexpected"
        if process.stdout is None or process.stdin is None:
            failure = "stream_unavailable"
            raise RuntimeError
        stdout = process.stdout
        lines: queue.Queue[bytes] = queue.Queue(maxsize=1)
        reader = threading.Thread(target=lambda: lines.put(stdout.readline(256)), daemon=True)
        reader.start()
        stage = "ready"
        try:
            line = lines.get(timeout=ready_timeout)
        except queue.Empty:
            failure = "ready_timeout"
            raise RuntimeError from None
        if line not in (b'{"ready":true}\n', b'{"ready":true}\r\n'):
            try:
                remote = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                failure = "invalid_ready"
            else:
                failure = dns_observer_remote_category(remote)
            raise RuntimeError
        timings["ready_ms"] = elapsed()
        stage = "probe"
        phase_started = time.monotonic()
        try:
            measured = run_probe()
        except Exception:
            failure = "probe_failed"
            raise RuntimeError from None
        if (
            not isinstance(measured, dict)
            or type(measured.get("fixed_udp_dns_response")) is not bool
        ):
            failure = "probe_result_invalid"
            raise RuntimeError
        timings["probe_ms"] = elapsed()
        if process.poll() is not None:
            failure = "observer_ended_before_probe"
            raise RuntimeError
        stage = "finish"
        phase_started = time.monotonic()
        failure = "completion_signal_failed"
        process.stdin.write(b"done\n")
        process.stdin.flush()
        process.stdin.close()
        output_lines: queue.Queue[bytes] = queue.Queue(maxsize=1)
        output_reader = threading.Thread(
            target=lambda: output_lines.put(stdout.read(4097)), daemon=True
        )
        output_reader.start()
        deadline = time.monotonic() + finish_timeout
        try:
            output = output_lines.get(timeout=finish_timeout)
        except queue.Empty:
            failure = "finish_timeout"
            raise RuntimeError from None
        if len(output) > 4096:
            failure = "output_limit"
            raise RuntimeError
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failure = "finish_timeout"
            raise RuntimeError from None
        try:
            payload = json.loads(output)
        except (ValueError, UnicodeDecodeError):
            failure = "invalid_result"
            raise RuntimeError from None
        if process.returncode != 0:
            failure = dns_observer_remote_category(payload)
            raise RuntimeError
        try:
            observation = sanitize_dns_observation(payload)
        except ValueError:
            failure = "invalid_result"
            raise RuntimeError from None
        observation["probe_response"] = measured["fixed_udp_dns_response"]
        timings["finish_ms"] = elapsed()
        observation["timing_ms"] = timings
        return observation
    except (OSError, RuntimeError, subprocess.SubprocessError):
        timings.setdefault(f"{stage}_ms", elapsed())
        return {"complete": False, "stage": stage, "reason": failure, "timing_ms": timings}
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        for thread in (reader, output_reader):
            if thread is not None:
                thread.join(timeout=1)
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if (
                    stream is not None
                    and not stream.closed
                    and not any(
                        thread is not None and thread.is_alive()
                        for thread in (reader, output_reader)
                    )
                ):
                    try:
                        stream.close()
                    except OSError:
                        pass


def record_dns_path_observation(
    report: dict[str, Any], collector: Callable[[], dict[str, Any]]
) -> dict[str, bool]:
    """Persist a fixed outcome even when the diagnostic itself fails unexpectedly."""
    report["dns_path_action_state"] = "started"
    try:
        observation = collector()
        if not isinstance(observation, dict) or type(observation.get("complete")) is not bool:
            raise ValueError("invalid_diagnostic_result")
    except Exception:
        observation = {"complete": False, "stage": "internal", "reason": "unexpected"}
    report["dns_path_observation"] = observation
    report["dns_path_action_state"] = "completed"
    return {"diagnostic_collected": observation["complete"]}


def summarize_probe_runner(
    output: str, exit_code: int | None, *, timed_out: bool = False
) -> dict[str, Any]:
    """Keep only fixed instrumentation health fields; never persist raw output."""
    lines = output.splitlines()
    completion_seen = any(line == "INSTRUMENTATION_RESULT: probe_complete=true" for line in lines)
    aborted_seen = any(
        line in {"INSTRUMENTATION_ABORTED", "INSTRUMENTATION_FAILED"} for line in lines
    )
    stages = [
        match.group(1)
        for line in lines
        if (match := re.fullmatch(r"INSTRUMENTATION_STATUS: i028_stage=([a-z0-9_]{1,40})", line))
        and match.group(1) in PROBE_STAGE_MARKERS
    ]
    if timed_out:
        category = "adb_timeout"
    elif exit_code is None:
        category = "adb_exit_unknown"
    elif exit_code != 0:
        category = "adb_nonzero"
    elif aborted_seen:
        category = "instrumentation_aborted"
    elif completion_seen:
        category = "completed"
    else:
        category = "completion_marker_missing"
    return {
        "adb_exit_code": (
            exit_code if exit_code is not None and -255 <= exit_code <= 255 else None
        ),
        "instrumentation_result_line_count": min(
            sum(line.startswith("INSTRUMENTATION_RESULT: ") for line in lines), 100
        ),
        "completion_marker_seen": completion_seen,
        "aborted_marker_seen": aborted_seen,
        "last_stage": stages[-1] if stages else None,
        "category": category,
    }


PROBE_STAGE_MARKERS = frozenset(
    {
        "started",
        "idle",
        "ipv4_primary_started",
        "ipv4_primary_done",
        "ipv4_fallback_started",
        "ipv4_fallback_done",
        "ipv4_done",
        "ipv6_started",
        "ipv6_done",
        "udp_dns_started",
        "udp_dns_done",
        "metadata_started",
        "metadata_done",
        "fixed_path_diagnostic_started",
        "fixed_path_diagnostic_done",
        "completed",
    }
)
MAX_PROBE_PARTIAL_OUTPUT_BYTES = 65536


def bounded_probe_partial_output(error: subprocess.TimeoutExpired) -> str:
    """Decode only a bounded timeout tail so fixed markers survive; never persist raw text."""
    parts: list[str] = []
    for value in (error.stdout, error.stderr):
        if isinstance(value, bytes):
            parts.append(value.decode("utf-8", errors="replace"))
        elif isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)[-MAX_PROBE_PARTIAL_OUTPUT_BYTES:]


def test_i028_real_android(harness: Harness) -> None:
    h = harness
    suite = os.environ.get("VPN_I028_SUITE", "Main")
    lifecycle, phase_name, required_facts = i028_suite_config(suite)
    adb_exe = Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"
    serial = os.environ["VPN_I028_SERIAL"]
    client = Path(os.environ["VPN_I028_CLIENT_DIR"])
    flutter = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/toolchains/flutter/bin/flutter.bat"

    def adb(*args: str, timeout: int = 90) -> str:
        return command([str(adb_exe), "-s", serial, *args], timeout=timeout).decode(
            "utf-8", "replace"
        )

    def saved_session() -> bool:
        # This preflight runs before the isolated package is installed. Its
        # earlier inventory assertion rejects an existing package, so there
        # cannot be app-private session data to inspect when it is absent.
        if f"package:{PACKAGE}" not in adb("shell", "pm", "list", "packages", PACKAGE).splitlines():
            return False
        if "no_backup" not in adb("shell", "run-as", PACKAGE, "ls", "-a").splitlines():
            return False
        return "session.vault" in adb("shell", "run-as", PACKAGE, "ls", "no_backup").splitlines()

    def installed_apk_matches(package: str, artifact: str) -> bool:
        package_path = adb("shell", "pm", "path", package).splitlines()
        if len(package_path) != 1:
            return False
        match = re.fullmatch(r"package:(/data/app/[A-Za-z0-9_./=+~-]+/base\.apk)", package_path[0])
        if match is None:
            return False
        installed_hash = adb("shell", "sha256sum", match[1]).split()[0]
        return installed_hash == hashlib.sha256(Path(artifact).read_bytes()).hexdigest()

    assert adb("get-state").strip() == "device"
    probe_installed = "package:org.example.vpnpoc.test" in adb(
        "shell", "pm", "list", "packages", "org.example.vpnpoc.test"
    )
    if suite == "NaturalSleep":
        # Play Protect approval happens before the 30-minute A window. Verify
        # both installed test APKs are exactly the source-bound artifacts.
        assert probe_installed
        assert installed_apk_matches("org.example.vpnpoc.test", os.environ["VPN_I028_PROBE_APK"])
        assert installed_apk_matches(TEST_PACKAGE, os.environ["VPN_I028_NATURAL_TEST_APK"])
    else:
        assert not probe_installed
        assert f"package:{TEST_PACKAGE}" not in adb("shell", "pm", "list", "packages", TEST_PACKAGE)
    # The isolated debug variant must be fresh. Never overwrite an earlier
    # I-028 session whose private test state this runner cannot safely recover.
    assert f"package:{PACKAGE}" not in adb("shell", "pm", "list", "packages", PACKAGE)
    assert "tcp:18766" not in adb("reverse", "--list")
    assert "package:org.example.vpnpoc" in adb(
        "shell", "pm", "list", "packages", "org.example.vpnpoc"
    )
    # Never erase an existing real account or alter another VPN's always-on policy.
    assert adb("shell", "settings", "get", "secure", "always_on_vpn_app").strip() == "null"
    # dumpsys retains old NetworkCapabilities even after disconnect; the independent
    # probe below checks the actual active network instead of that stale structure.
    assert not saved_session()
    installed_before = (
        f"package:{PACKAGE}" in adb("shell", "pm", "list", "packages", PACKAGE).splitlines()
    )
    previous_mode = "default"
    if installed_before:
        previous_appop = adb("shell", "appops", "get", PACKAGE, "ACTIVATE_VPN")
        mode_match = re.search(r"ACTIVATE_VPN: (allow|deny|ignore|default)", previous_appop)
        previous_mode = mode_match[1] if mode_match else "default"
    status = helper("status")
    assert status.get("prepared") and status.get("remaining_seconds", 0) >= 1100
    assert status.get("live_slots") == [] and status.get("peer_counts") == [0, 0]
    report: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "scope": "bounded staging A, one physical Android, local disposable API/PostgreSQL",
        "suite": suite,
        "diagnostic_only": suite == "Egress",
        "vpn_dataplane_acceptance": False,
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "services/api/tests/test_i028_live.py",
                "scripts/run_i028_staging.ps1",
                "scripts/i028_device_scenarios.py",
                "apps/client/integration_test/i028_live_test.dart",
                "apps/client/android/app/build.gradle.kts",
                "apps/client/android/app/gradle.lockfile",
                "apps/client/android/app/src/androidTest/java/org/example/vpnpoc/I028FlutterInstrumentationTest.java",
                "apps/client/android/app/src/main/java/org/example/vpnpoc/ClientActivity.java",
                "apps/android/app/src/androidTest/java/org/example/vpnpoc/I028Probe.java",
                "infra/staging-v3/runtime.py",
                "infra/staging-v3/dns_observer.py",
            )
        },
        "telegram": "TEST FAKE ONLY",
        "quota_funding": "TEST FREE GRANT ONLY",
        "facts": {},
        "open_cases": [
            "natural sleep and forced Doze",
            "Wi-Fi/mobile transition",
            "system VPN permission revoke",
            "Disconnect during in-flight renewal",
            "two simultaneous physical devices",
            "independent provider failure",
            "comprehensive DNS leak capture",
        ],
    }
    h.settings.enable_window_tunnels = True
    h.settings.tunnel_profile_file = None
    state: dict[str, Any] = {"outage": False}
    renewal_arrived = threading.Event()
    renewal_release = threading.Event()
    natural_sleep_reattached = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job: concurrent.futures.Future[dict[str, Any]] | None = None
    api_thread: threading.Thread | None = None
    tunnel: subprocess.Popen[bytes] | None = None
    instrumentation: subprocess.Popen[bytes] | None = None
    bridge: Any = None
    server: uvicorn.Server | None = None
    agent_started = False
    phone_modified = False
    device_scenarios: Any = None
    control_socket = "/home/ad/.ssh/detourvpn-i028-" + uuid4().hex + ".socket"
    stage = "private_manifest"
    with tempfile.TemporaryDirectory(
        prefix="i028-", dir=Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn"
    ) as directory:
        private = Path(directory)
        owner = command(["whoami.exe"]).decode().strip()
        command(
            [
                "icacls.exe",
                str(private),
                "/inheritance:r",
                "/grant:r",
                owner + ":(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ]
        )
        try:
            provisioned = NodeRegistry(h.engine).provision(
                ProvisionNodeInput(
                    idempotency_key=uuid4(),
                    name="I028 bounded staging A",
                    region="staging",
                )
            )
            assert provisioned.secret is not None
            manifest = {
                "version": 3,
                "nodes": [{"node_id": str(provisioned.node_id), **helper("manifest")}],
            }
            manifest_path = private / "windows.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            del manifest
            h.settings.window_manifest_file = manifest_path
            helper(
                "configure", {"node_id": str(provisioned.node_id), "credential": provisioned.secret}
            )
            app = create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender)

            def principal(request: Request) -> Principal:
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                with transaction(h.engine) as session:
                    p = h.auth.authenticate(session, token, "tunnels:manage")
                    if "user" in state:
                        assert (p.user_id, p.family_id) == (state["user"], state["family"])
                    return p

            def device_id(p: Principal) -> Any:
                with Session(h.engine) as session:
                    family = session.get(SessionFamily, p.family_id)
                    assert family is not None and family.device_id is not None
                    return family.device_id

            @app.middleware("http")
            async def outage(request: Request, call_next: RequestResponseEndpoint) -> Response:
                if state["outage"] and request.url.path.startswith(
                    ("/api/v1/window-tunnels", "/api/internal/v3/windows")
                ):
                    return Response(status_code=503)
                response = await call_next(request)
                if (
                    state.get("hold_renewal")
                    and request.method == "POST"
                    and request.url.path.endswith("/renew")
                    and response.status_code == 202
                ):
                    state["hold_renewal"] = False
                    renewal_arrived.set()
                    # TEST FAULT ONLY: real response/agent/DB, bounded below client timeout.
                    for _ in range(60):
                        if renewal_release.is_set():
                            break
                        await asyncio.sleep(0.1)
                    report["renewal_response_released_by_test"] = renewal_release.is_set()
                return response

            @app.post("/api/v1/__i028__/renewal-race/{verb}", include_in_schema=False)
            def renewal_race(request: Request, verb: str) -> dict[str, bool]:
                principal(request)
                assert verb in {"arm", "status", "release", "verify"}
                if verb == "arm":
                    assert not state.get("hold_renewal") and not renewal_arrived.is_set()
                    state["hold_renewal"] = True
                elif verb == "release":
                    assert renewal_arrived.is_set()
                    renewal_release.set()
                elif verb == "verify":
                    assert renewal_arrived.is_set() and renewal_release.is_set()
                    with transaction(h.engine) as session:
                        rows = list(session.scalars(select(PeerConnection)))
                        assert rows and all(row.state == "closed" for row in rows)
                        ledger = LedgerService(h.engine)
                        quota = ledger.quota(session, state["user"])
                        checked = ledger.reconcile(session, state["user"])
                        assert quota.reserved_bytes == 0 and checked.consistent
                        assert checked.violations == 0
                    remote = helper("status")
                    assert remote.get("live_slots") == []
                    assert remote.get("peer_counts") == [0, 0]
                    report["renewal_race_server_closed_zero_holds_and_peers"] = True
                acknowledged = False
                if renewal_arrived.is_set():
                    with Session(h.engine) as session:
                        connection = session.scalar(
                            select(PeerConnection).order_by(PeerConnection.created_at.desc())
                        )
                        acknowledged = (
                            connection is not None
                            and connection.state == "active"
                            and connection.generation >= 2
                        )
                    if acknowledged and verb == "status":
                        report["renewal_race_real_node_ack_before_disconnect"] = True
                return {"arrived": renewal_arrived.is_set() and acknowledged}

            @app.post("/api/v1/__i028__/issue", include_in_schema=False)
            def issue_code() -> dict[str, str]:
                with Session(h.engine) as session:
                    attempt = session.scalar(
                        select(LoginAttempt).order_by(LoginAttempt.created_at.desc())
                    )
                    assert attempt is not None
                    start = StartResponse(
                        request_id=attempt.id,
                        verifier="TEST UNUSED",
                        bot_url="https://t.me/LocalTestLoginBot",
                        expires_at=attempt.expires_at,
                    )
                return {"code": issue(h, start, subject=2**40 + 2800)}

            @app.post("/api/v1/__i028__/seed", include_in_schema=False)
            def seed(request: Request, value: dict[str, int]) -> dict[str, bool]:
                p = principal(request)
                assert "user" not in state
                state.update(
                    user=p.user_id, device=device_id(p), family=p.family_id, pid=value["pid"]
                )
                LedgerService(h.engine).apply(p.user_id, event("credit", 512 * 1024 * 1024))
                return {"accepted": True}

            @app.post("/api/v1/__i028__/restored", include_in_schema=False)
            def restored(request: Request, value: dict[str, int]) -> dict[str, bool]:
                p = principal(request)
                assert (p.user_id, device_id(p), p.family_id) == (
                    state["user"],
                    state["device"],
                    state["family"],
                )
                assert value["pid"] != state["pid"]
                report["different_android_process_same_real_account_family_and_device"] = True
                return {"accepted": True}

            def probe(mode: str) -> dict[str, Any]:
                try:
                    result = subprocess.run(  # noqa: S603 - fixed test-only instrumentation command.
                        [
                            str(adb_exe),
                            "-s",
                            serial,
                            "shell",
                            "am",
                            "instrument",
                            "-w",
                            "-r",
                            "-e",
                            "mode",
                            mode,
                            "org.example.vpnpoc.test/org.example.vpnpoc.I028Probe",
                        ],
                        capture_output=True,
                        timeout=60 if mode == "egress" else 45,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    report["last_probe_runner"] = summarize_probe_runner(
                        bounded_probe_partial_output(exc), None, timed_out=True
                    )
                    raise RuntimeError("i028_probe_command_timeout") from None
                except OSError:
                    report["last_probe_runner"] = {
                        "adb_exit_code": None,
                        "instrumentation_result_line_count": 0,
                        "completion_marker_seen": False,
                        "aborted_marker_seen": False,
                        "category": "adb_unavailable",
                    }
                    raise RuntimeError("i028_probe_command_failed") from None
                output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
                report["last_probe_runner"] = summarize_probe_runner(output, result.returncode)
                if result.returncode != 0:
                    raise RuntimeError("i028_probe_command_failed")
                parsed_result = parse_probe(output)
                report.setdefault("independent_probes", []).append(parsed_result)
                return parsed_result

            def action_work(name: str) -> dict[str, Any]:
                if name == "natural_sleep":
                    assert suite == "NaturalSleep" and device_module is not None
                    timeout_ms = int(
                        adb("shell", "settings", "get", "system", "screen_off_timeout")
                    )
                    assert 15000 <= timeout_ms <= 60000
                    before_idle = adb("shell", "dumpsys", "deviceidle")
                    assert "mForceIdle=false" in before_idle
                    assert "mDeepEnabled=true" in before_idle
                    assert "UPDATES STOPPED" not in adb("shell", "dumpsys", "battery")
                    started = time.monotonic()
                    (ROOT / ".cache/i028-natural-sleep-ready.json").write_text(
                        json.dumps(
                            {"owner_action_required": True, "screen_timeout_ms": timeout_ms}
                        ),
                        encoding="utf-8",
                    )
                    detached_at: float | None = None
                    for _ in range(30):
                        try:
                            adb("get-state", timeout=5)
                        except (RuntimeError, subprocess.SubprocessError):
                            detached_at = time.monotonic()
                            break
                        time.sleep(4)
                    assert detached_at is not None
                    # Do not poll the phone during real battery-powered sleep.
                    time.sleep(180)
                    remote = helper("status")
                    server_closed = remote.get("live_slots") == [] and remote.get(
                        "peer_counts"
                    ) == [0, 0]
                    report["natural_sleep_server_observation"] = {
                        "elapsed_after_detach_seconds": int(time.monotonic() - detached_at),
                        "live_slot_count": len(remote.get("live_slots", [])),
                        "peer_counts": remote.get("peer_counts"),
                        "closed": server_closed,
                    }
                    while time.monotonic() - detached_at < 390:
                        time.sleep(5)
                    reattached_at: float | None = None
                    for _ in range(60):
                        try:
                            if adb("get-state", timeout=5).strip() == "device":
                                reattached_at = time.monotonic()
                                break
                        except (RuntimeError, subprocess.SubprocessError):
                            pass
                        time.sleep(5)
                    assert reattached_at is not None
                    after_idle = adb("shell", "dumpsys", "deviceidle")
                    idle_observed = device_module.natural_deep_idle_observed(
                        before_idle, after_idle, reattached_at - started
                    )
                    adb("reverse", "tcp:18766", "tcp:18766")
                    adb(
                        "shell",
                        "am",
                        "start",
                        "-n",
                        f"{PACKAGE}/org.example.vpnpoc.ClientActivity",
                    )
                    report["natural_sleep_observation"] = {
                        "usb_absent_confirmed": True,
                        "unplugged_seconds": int(reattached_at - detached_at),
                        "forced_idle_absent": "mForceIdle=false" in after_idle,
                        "natural_deep_idle_observed": idle_observed,
                    }
                    natural_sleep_reattached.set()
                    return {
                        "usb_absent_confirmed": True,
                        "natural_idle_observed": idle_observed,
                        "server_closed_during_sleep": server_closed,
                    }
                if name in {"background", "screen_off", "forced_doze"}:
                    assert lifecycle and device_scenarios is not None

                    def observe_windows() -> dict[str, Any]:
                        remote = helper("status")
                        with Session(h.engine) as session:
                            valid_slots = set()
                            for connection in session.scalars(select(PeerConnection)):
                                hold = session.get(PeerWindowHold, connection.window_id)
                                if hold is not None and hold.not_after > datetime.now(UTC):
                                    valid_slots.add(connection.slot_id)
                        valid = all(slot in valid_slots for slot in remote.get("live_slots", []))
                        remote["live_slots_have_unexpired_current_window"] = valid
                        assert valid
                        return remote

                    try:
                        value = device_scenarios.suspend(name, observe_windows)
                    finally:
                        report.setdefault("lifecycle_observations", {})[name] = (
                            device_scenarios.last_observation
                        )
                    assert value["elapsed_seconds"] >= 100
                    assert all(value["restoration"].values())
                    return {"sleep_done": True}
                if name in {"mobile", "wifi", "loss"}:
                    assert lifecycle and device_scenarios is not None
                    device_scenarios.network(name)
                    if name == "loss":
                        confirmed = device_module.radio_loss_confirmed(
                            adb("shell", "cmd", "wifi", "status"),
                            adb("shell", "dumpsys", "telephony.registry"),
                        )
                        report["temporary_network_loss_radio_state_confirmed"] = confirmed
                        return {"both_radios_disabled": confirmed}
                    return probe("all")
                if name == "open_vpn_settings":
                    assert lifecycle
                    adb("shell", "am", "start", "-a", "android.settings.VPN_SETTINGS")
                    (ROOT / ".cache/i028-system-revoke-pending.json").write_text(
                        json.dumps(
                            {
                                "owner_action_required": True,
                                "measured_at": datetime.now(UTC).isoformat(),
                            }
                        ),
                        encoding="utf-8",
                    )
                    return {"settings_opened": True}
                if name == "permission_revoked":
                    assert lifecycle
                    mode = adb("shell", "appops", "get", PACKAGE, "ACTIVATE_VPN")
                    match = re.search(r"ACTIVATE_VPN: (allow|deny|ignore|default)", mode)
                    revoked = match is not None and match[1] != "allow"
                    if revoked:
                        report["system_permission_revoked_observed"] = True
                        adb(
                            "shell",
                            "am",
                            "start",
                            "-n",
                            f"{PACKAGE}/org.example.vpnpoc.ClientActivity",
                        )
                        report["system_revoke_test_app_foregrounded"] = True
                    return {"permission_revoked": revoked}
                if name in {"recovery_probe_ipv4", "egress_probe_all", "probe_ipv4"}:
                    kind = {
                        "egress_probe_all": "egress",
                        "probe_ipv4": "sustained",
                        "recovery_probe_ipv4": "recovery",
                    }[name]

                    def record_node(phase: str) -> None:
                        try:
                            report[f"{kind}_node_{phase}_probe"] = sanitize_recovery_node_status(
                                helper("status", timeout=10)
                            )
                        except Exception:
                            # Diagnostic failure must not alter the physical acceptance result.
                            report[f"{kind}_node_{phase}_probe"] = {"available": False}

                    def record_connection(phase: str) -> None:
                        try:
                            with Session(h.engine) as session:
                                snapshot = recovery_connection_snapshot(session, state["user"])
                            report[f"{kind}_connection_{phase}_probe"] = snapshot
                        except Exception:
                            # Diagnostic failure must never change the probe outcome.
                            report[f"{kind}_connection_{phase}_probe"] = {"available": False}

                    record_node("before")
                    record_connection("before")
                    try:
                        return probe("egress" if kind == "egress" else "ipv4")
                    finally:
                        record_node("after")
                        record_connection("after")
                if name == "egress_dns_observe":
                    if suite != "Egress":
                        raise RuntimeError("i028_dns_observe_wrong_suite")

                    def record_dns_node(point: str) -> None:
                        try:
                            report[f"dns_observer_node_{point}"] = sanitize_recovery_node_status(
                                helper("status", timeout=10)
                            )
                        except Exception:
                            report[f"dns_observer_node_{point}"] = {"available": False}

                    record_dns_node("before")
                    try:
                        return record_dns_path_observation(
                            report,
                            lambda: collect_dns_path_observation(
                                [*SSH, HOST, HELPER + "dns-observe"], lambda: probe("dns")
                            ),
                        )
                    finally:
                        record_dns_node("after")
                if name.startswith("probe_"):
                    return probe("all" if name == "probe_all" else "ipv4")
                if name == "outage":
                    state["outage"] = True
                    helper("crash-agent")
                    return {"outage_started": True}
                if name == "expired":
                    value = helper("status")
                    return {
                        "kernel_closed": value.get("live_slots") == []
                        and not value.get("agent_active")
                    }
                assert name == "recover"
                state["outage"] = False
                helper("start-agent")
                for _ in range(60):
                    with Session(h.engine) as session:
                        count = session.scalar(select(func.count()).select_from(PeerRuntimeEpoch))
                        node = session.get(VPNNode, provisioned.node_id)
                        if count == 2 and node is not None and node.peer_runtime_id is not None:
                            return {"recovered": True}
                    time.sleep(0.5)
                raise RuntimeError("i028_recovery_unconfirmed")

            @app.get("/api/v1/__i028__/egress-renewal-status", include_in_schema=False)
            def egress_renewal_status(request: Request) -> dict[str, Any]:
                p = principal(request)
                assert suite == "Egress"
                with Session(h.engine) as session:
                    connection = session.scalar(
                        select(PeerConnection)
                        .where(PeerConnection.user_id == p.user_id)
                        .order_by(PeerConnection.created_at.desc())
                    )
                    generation = (
                        connection.generation
                        if connection is not None
                        and type(connection.generation) is int
                        and 0 <= connection.generation <= 2**63 - 1
                        else None
                    )
                    state_name = (
                        connection.state
                        if connection is not None
                        and connection.state in {"active", "pending", "closed", "revoke_pending"}
                        else "other"
                    )
                status = sanitize_egress_renewal_status(
                    {
                        "available": True,
                        "generation": generation,
                        "state": state_name,
                    }
                )
                report["egress_renewal_status"] = status
                return status

            @app.post("/api/v1/__i028__/action/{name}", include_in_schema=False)
            def action(request: Request, name: str) -> dict[str, bool]:
                nonlocal job
                principal(request)
                assert name in {
                    "probe_all",
                    "probe_ipv4",
                    "recovery_probe_ipv4",
                    "egress_probe_all",
                    "egress_dns_observe",
                    "outage",
                    "expired",
                    "recover",
                    "background",
                    "screen_off",
                    "forced_doze",
                    "natural_sleep",
                    "mobile",
                    "wifi",
                    "loss",
                    "open_vpn_settings",
                    "permission_revoked",
                }
                assert job is None or job.done()
                if name == "egress_dns_observe":
                    report["dns_path_action_state"] = "queued"
                job = executor.submit(action_work, name)
                return {"queued": True}

            @app.get("/api/v1/__i028__/job", include_in_schema=False)
            def job_result(request: Request) -> dict[str, Any]:
                principal(request)
                assert job is not None
                if not job.done():
                    return {"done": False}
                try:
                    return {"done": True, "ok": True, **job.result()}
                except Exception:
                    return {"done": True, "ok": False}

            @app.post("/api/v1/__i028__/fact", include_in_schema=False)
            def fact(request: Request, value: dict[str, str]) -> dict[str, bool]:
                principal(request)
                assert value["name"] in (
                    FACTS
                    | EGRESS_DIAGNOSTIC_FACTS
                    | LIFECYCLE_FACTS
                    | NETWORK_REVOKE_FACTS
                    | NATURAL_SLEEP_FACTS
                    | {
                        "direct_ipv4_comparison_unavailable",
                        "native_reports_fresh_handshake",
                        "advisory_traffic_renewal_requested",
                        "mobile_direct_ipv6_baseline_available",
                    }
                )
                report["facts"][value["name"]] = True
                return {"accepted": True}

            @app.post("/api/v1/__i028__/renewals", include_in_schema=False)
            def renewals(request: Request) -> dict[str, bool]:
                principal(request)
                with Session(h.engine) as session:
                    connection = session.scalar(
                        select(PeerConnection).order_by(PeerConnection.created_at.desc())
                    )
                    generation = (
                        connection.generation
                        if connection is not None
                        and type(connection.generation) is int
                        and 0 <= connection.generation <= 2**63 - 1
                        else None
                    )
                    state_name = (
                        connection.state
                        if connection is not None
                        and connection.state in {"active", "pending", "closed", "revoke_pending"}
                        else "other"
                    )
                    report["sustained_renewal_server_check"] = {
                        "connection_present": connection is not None,
                        "generation": generation,
                        "state": state_name,
                    }
                    assert (
                        connection is not None
                        and connection.generation >= 4
                        and connection.state == "active"
                    )
                    report["sustained_acknowledged_generation"] = connection.generation
                return {"accepted": True}

            @app.post("/api/v1/__i028__/native_observation_after_failure", include_in_schema=False)
            @app.post("/api/v1/__i028__/native_recovery_before", include_in_schema=False)
            @app.post("/api/v1/__i028__/native_recovery_after", include_in_schema=False)
            @app.post("/api/v1/__i028__/native_egress_before", include_in_schema=False)
            @app.post("/api/v1/__i028__/native_egress_after", include_in_schema=False)
            @app.post("/api/v1/__i028__/native_observation", include_in_schema=False)
            def native_observation(request: Request, value: dict[str, Any]) -> dict[str, bool]:
                principal(request)
                assert set(value) == {"up", "network", "handshake_age", "remaining_ms"}
                assert type(value["up"]) is bool and type(value["network"]) is bool
                assert type(value["handshake_age"]) is int and -1 <= value["handshake_age"] <= 86400
                assert type(value["remaining_ms"]) is int and 0 <= value["remaining_ms"] <= 90000
                key = {
                    "native_observation_after_failure": "native_observation_after_failed_transfer",
                    "native_recovery_before": "native_recovery_before_probe",
                    "native_recovery_after": "native_recovery_after_probe",
                    "native_egress_before": "native_egress_before_probe",
                    "native_egress_after": "native_egress_after_probe",
                    "native_observation": "last_sustained_native_observation",
                }[request.url.path.rsplit("/", 1)[-1]]
                report[key] = value
                if key == "last_sustained_native_observation" and suite == "Main":
                    try:
                        with Session(h.engine) as session:
                            connection = recovery_connection_snapshot(session, state["user"])
                    except Exception:
                        connection = {"available": False}
                    append_sustained_window_observation(report, value, connection)
                return {"accepted": True}

            @app.post("/api/v1/__i028__/egress-hold-observation", include_in_schema=False)
            def egress_hold_observation(request: Request, value: dict[str, Any]) -> dict[str, bool]:
                principal(request)
                observation = sanitize_egress_hold_observations(value)
                report["egress_hold_observation"] = observation
                return {"accepted": True}

            @app.post("/api/v1/__i028__/revoke", include_in_schema=False)
            def revoke(request: Request) -> dict[str, bool]:
                p = principal(request)
                with transaction(h.engine) as session:
                    rows = session.scalars(
                        select(PeerConnection).where(
                            PeerConnection.user_id == p.user_id, PeerConnection.state != "closed"
                        )
                    )
                    for row in rows:
                        row.state = "revoke_pending"
                return {"accepted": True}

            @app.post("/api/v1/__i028__/verify", include_in_schema=False)
            def verify(request: Request) -> dict[str, bool]:
                p = principal(request)
                with transaction(h.engine) as session:
                    assert not list(
                        session.scalars(
                            select(PeerConnection).where(PeerConnection.state != "closed")
                        )
                    )
                    receipts = list(session.scalars(select(PeerWindowReceipt)))
                    assert receipts and all(r.observation == "measured" for r in receipts)
                    measured = sum(r.observed_bytes or 0 for r in receipts)
                    ledger = LedgerService(h.engine)
                    quota = ledger.quota(session, p.user_id)
                    result = ledger.reconcile(session, p.user_id)
                    assert measured > 0 and result.consistent and result.violations == 0
                    assert quota.reserved_bytes == 0 and quota.used_bytes == measured
                    report.update(
                        exact_measured_bytes=str(measured),
                        receipt_count=len(receipts),
                        reserved_bytes="0",
                        ledger_consistent=True,
                    )
                return {"accepted": True}

            stage = "start_local_transport"
            spec = importlib.util.spec_from_file_location(
                "i028_pipe", ROOT / "scripts/i028_pipe_relay.py"
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
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
            api_thread = threading.Thread(target=server.run, daemon=True)
            api_thread.start()
            for _ in range(60):
                if server.started:
                    break
                time.sleep(0.1)
            assert server.started
            bridge = module.PipeRelay(ROOT / "scripts/i028_pipe_relay.py")
            tunnel = subprocess.Popen(  # noqa: S603 - fixed scoped reverse tunnel.
                [
                    *SSH,
                    "-N",
                    "-T",
                    "-M",
                    "-S",
                    control_socket,
                    "-o",
                    "ExitOnForwardFailure=yes",  # noqa: S603
                    "-o",
                    "ServerAliveInterval=10",
                    "-o",
                    "ServerAliveCountMax=2",
                    "-R",
                    "127.0.0.1:18443:127.0.0.1:18767",
                    HOST,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            assert tunnel.poll() is None
            helper("start-agent")
            agent_started = True
            for _ in range(60):
                with Session(h.engine) as session:
                    node = session.get(VPNNode, provisioned.node_id)
                    if node is not None and node.peer_runtime_id is not None:
                        break
                time.sleep(0.5)
            else:
                raise RuntimeError("i028_authenticated_agent_not_ready")
            stage = "phone_setup"
            phone_modified = True
            adb("reverse", "tcp:18766", "tcp:18766")
            if suite == "NaturalSleep":
                report["preinstalled_probe_apk_source_hash_verified"] = True
            else:
                adb(
                    "install",
                    "-r",
                    os.environ["VPN_I028_PROBE_APK"],
                )
            if lifecycle:
                device_spec = importlib.util.spec_from_file_location(
                    "i028_device", ROOT / "scripts/i028_device_scenarios.py"
                )
                assert device_spec is not None and device_spec.loader is not None
                device_module = importlib.util.module_from_spec(device_spec)
                device_spec.loader.exec_module(device_module)
                device_scenarios = device_module.DeviceScenarios(adb)
            env = dict(
                os.environ,
                JAVA_HOME=r"C:\Program Files\Android\Android Studio\jbr",
                PUB_CACHE=str(flutter.parents[2] / "pub-cache"),
            )
            for phase in ("seed", phase_name):
                stage = "flutter_" + phase
                if phase == "natural_sleep":
                    marker_path = "no_backup/i028-natural-sleep-stage.txt"
                    diagnostics_path = "no_backup/i028-natural-sleep-diagnostics.jsonl"

                    def phone_stage(path: str = marker_path) -> str | None:
                        try:
                            value = adb("shell", "run-as", PACKAGE, "cat", path, timeout=10).strip()
                        except (RuntimeError, subprocess.SubprocessError):
                            return None
                        return value if re.fullmatch(r"[a-z_]{1,64}", value) else None

                    def collect_phone_diagnostics(path: str = diagnostics_path) -> None:
                        try:
                            raw = adb("shell", "run-as", PACKAGE, "cat", path, timeout=10)
                            parsed = device_module.parse_phone_diagnostics(raw)
                            report["phone_wake_diagnostics"] = parsed
                            if parsed.get("last_stage") is not None:
                                report["phone_instrumentation_last_stage"] = parsed["last_stage"]
                            try:
                                process = adb("shell", "pidof", "-s", PACKAGE, timeout=10).strip()
                                report["phone_app_process_pid"] = (
                                    int(process) if process.isdecimal() else None
                                )
                            except (RuntimeError, subprocess.SubprocessError):
                                report["phone_app_process_pid"] = None
                        except (RuntimeError, subprocess.SubprocessError):
                            report["phone_wake_diagnostics"] = {
                                "valid": False,
                                "reason": "unavailable_after_reattach",
                                "events": [],
                            }

                    adb("shell", "run-as", PACKAGE, "rm", "-f", marker_path)
                    adb("shell", "run-as", PACKAGE, "rm", "-f", diagnostics_path)
                    adb("install", "-r", os.environ["VPN_I028_NATURAL_APP_APK"])
                    report["preinstalled_instrumentation_apk_source_hash_verified"] = True
                    instrumentation = subprocess.Popen(  # noqa: S603 - fixed Android test runner.
                        [
                            str(adb_exe),
                            "-s",
                            serial,
                            "shell",
                            "am",
                            "instrument",
                            "-w",
                            "-r",
                            "-e",
                            "class",
                            "org.example.vpnpoc.I028FlutterInstrumentationTest",
                            f"{TEST_PACKAGE}/androidx.test.runner.AndroidJUnitRunner",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    deadline = time.monotonic() + 850
                    initial_deadline = time.monotonic() + 150
                    while True:
                        current = phone_stage()
                        if current == "natural_sleep_wait":
                            break
                        if current is not None and current.startswith("failed_"):
                            report["phone_failure_stage"] = current.removeprefix("failed_")
                            raise RuntimeError("i028_phone_instrumentation_failed")
                        if (
                            instrumentation.poll() is not None
                            or time.monotonic() > initial_deadline
                        ):
                            if instrumentation.poll() is not None:
                                report["adb_instrument_command_exit_code"] = (
                                    instrumentation.returncode
                                )
                                report["phone_instrumentation_last_stage"] = current
                            raise RuntimeError("i028_phone_instrumentation_did_not_start")
                        time.sleep(2)
                    # The device worker owns the unplugged interval. Never poll ADB during sleep.
                    while not natural_sleep_reattached.wait(2):
                        if job is not None and job.done():
                            try:
                                job.result()
                            except Exception:
                                raise RuntimeError(
                                    "i028_natural_sleep_device_worker_failed"
                                ) from None
                        if time.monotonic() > deadline:
                            raise RuntimeError("i028_natural_sleep_reattach_timeout")
                    # Reattachment is not the same as the operator waking and
                    # unlocking the phone. Give Android a separate bounded
                    # recovery window instead of spending it during deep idle.
                    completion_deadline = time.monotonic() + 300
                    while True:
                        collect_phone_diagnostics()
                        current = phone_stage()
                        if current == "complete":
                            report["phone_on_device_instrumentation_complete"] = True
                            break
                        if current is not None and current.startswith("failed_"):
                            report["phone_failure_stage"] = current.removeprefix("failed_")
                            raise RuntimeError("i028_phone_instrumentation_failed")
                        if current is not None:
                            report["phone_instrumentation_last_stage"] = current
                        if instrumentation.poll() is not None:
                            exit_code = instrumentation.returncode
                            report["adb_instrument_command_exit_code"] = exit_code
                            # Unplugging USB can end the host-side adb shell
                            # while the on-device instrumentation is still
                            # sleeping. After verified reattachment, trust only
                            # the bounded app-private stage marker; don't start
                            # cleanup until it completes or the recovery deadline.
                            if exit_code != 255:
                                raise RuntimeError(
                                    "i028_phone_instrumentation_exited_before_complete"
                                )
                        if time.monotonic() > completion_deadline:
                            collect_phone_diagnostics()
                            raise RuntimeError("i028_phone_instrumentation_completion_timeout")
                        time.sleep(2)
                    continue
                result = subprocess.run(  # noqa: S603 - pinned Flutter/reviewed target.
                    [
                        str(flutter),
                        "test",
                        "integration_test/i028_live_test.dart",  # noqa: S603
                        "-d",
                        serial,
                        "--no-pub",
                        "--no-uninstall",
                        "--dart-define=VPN_WINDOW_TUNNELS=true",
                        "--dart-define=VPN_I028_PHASE=" + phase,
                    ],
                    cwd=client,
                    env=env,
                    capture_output=True,
                    timeout=850,
                    check=False,
                )
                failure = re.search(rb"i028_stage_([a-z_]{1,48})", result.stdout + result.stderr)
                if failure:
                    report["phone_failure_stage"] = failure[1].decode("ascii")
                report["flutter_exit_code"] = result.returncode
                report["flutter_stage_marker_seen"] = failure is not None
                success = result.returncode == 0
                del result
                # Pytest assertion rewriting must never render CompletedProcess,
                # whose captured output could contain a live test credential.
                if not success:
                    raise RuntimeError("i028_flutter_failed_see_sanitized_receipt")
                if phase == "seed":
                    report["seed_postflight_checkpoint"] = "package_inventory"
                    installed_packages = adb(
                        "shell", "pm", "list", "packages", PACKAGE
                    ).splitlines()
                    report["seed_package_installed"] = f"package:{PACKAGE}" in installed_packages
                    assert report["seed_package_installed"]
                    report["seed_postflight_checkpoint"] = "session_vault"
                    vault_entries = adb("shell", "run-as", PACKAGE, "ls", "no_backup").splitlines()
                    report["seed_session_vault_present"] = "session.vault" in vault_entries
                    assert report["seed_session_vault_present"]
                    report["seed_postflight_checkpoint"] = "force_stop"
                    adb("shell", "am", "force-stop", PACKAGE)
                    report["seed_postflight_checkpoint"] = "vpn_appop"
                    adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "allow")
                    report["seed_vpn_appop_set"] = True
                    report["seed_postflight_checkpoint"] = "complete"
            report["passed"] = all(report["facts"].get(name) is True for name in required_facts)
            report["passed"] &= report.get("ledger_consistent") is True
        finally:
            renewal_release.set()
            state["outage"] = False
            executor.shutdown(wait=True, cancel_futures=True)
            clean = True
            cleanup_steps: dict[str, Any] = {}
            report["cleanup_steps"] = cleanup_steps
            if device_scenarios is not None:
                cleanup_steps["device_scenarios_restored"] = device_scenarios.restore()
                clean &= all(cleanup_steps["device_scenarios_restored"].values())
            # Keep API/SSH alive until the agent closes and submits measured receipts.
            if agent_started:
                try:
                    report["node_before_cleanup"] = helper("status")
                    with transaction(h.engine) as session:
                        for row in session.scalars(
                            select(PeerConnection).where(PeerConnection.state != "closed")
                        ):
                            row.state = "revoke_pending"
                    if not helper("status").get("agent_active"):
                        helper("start-agent")
                    for _ in range(30):
                        with Session(h.engine) as session:
                            if (
                                session.scalar(
                                    select(func.count())
                                    .select_from(PeerConnection)
                                    .where(PeerConnection.state != "closed")
                                )
                                == 0
                            ):
                                break
                        time.sleep(0.5)
                    helper("stop-agent")
                    cleanup_steps["agent_revoke_and_stop"] = True
                except (RuntimeError, subprocess.SubprocessError):
                    cleanup_steps["agent_revoke_and_stop"] = False
                    clean = False
            if "user" in state:
                try:
                    with transaction(h.engine) as session:
                        ledger = LedgerService(h.engine)
                        quota = ledger.quota(session, state["user"])
                        checked = ledger.reconcile(session, state["user"])
                        receipts = list(session.scalars(select(PeerWindowReceipt)))
                        unknown = sum(row.observation == "unknown" for row in receipts)
                        open_count = session.scalar(
                            select(func.count())
                            .select_from(PeerConnection)
                            .where(PeerConnection.state != "closed")
                        )
                        report["accounting_after_revoke"] = {
                            "ledger_consistent": checked.consistent,
                            "violations": checked.violations,
                            "used_bytes": str(quota.used_bytes),
                            "reserved_bytes": str(quota.reserved_bytes),
                            "measured_receipt_bytes": str(
                                sum(row.observed_bytes or 0 for row in receipts)
                            ),
                            "receipt_count": len(receipts),
                            "unknown_receipts": unknown,
                            "open_connections": open_count,
                        }
                        report["control_plane_settlement_complete"] = (
                            checked.consistent
                            and checked.violations == 0
                            and quota.reserved_bytes == 0
                            and unknown == 0
                            and open_count == 0
                        )
                except Exception:
                    report["control_plane_settlement_complete"] = False
                report["passed"] &= report["control_plane_settlement_complete"]
            try:
                cleanup_steps["root_stop"] = helper("stop")
                clean &= cleanup_steps["root_stop"].get("cleanup_complete") is True
            except (RuntimeError, subprocess.SubprocessError):
                cleanup_steps["root_stop"] = False
                clean = False
            if tunnel is not None:
                try:
                    command([*SSH, "-S", control_socket, "-O", "exit", HOST], timeout=15)
                    tunnel.wait(timeout=10)
                    cleanup_steps["ssh_transport"] = True
                except (RuntimeError, subprocess.SubprocessError):
                    tunnel.kill()
                    tunnel.wait(timeout=5)
                    clean = False
                    cleanup_steps["ssh_transport"] = False
            try:
                baseline = command(
                    [*SSH, HOST, "ip netns list && ip -o link show && ss -H -lun && ss -H -ltn"],
                    timeout=20,
                )
                remote_clean = all(
                    value not in baseline
                    for value in (
                        b"dv3slot",
                        b"dv3h1",
                        b"dv3h2",
                        b":51901 ",
                        b":51902 ",
                        b":18443 ",
                    )
                )
                report["independent_unprivileged_remote_postcheck"] = remote_clean
                clean &= remote_clean
            except (RuntimeError, subprocess.SubprocessError):
                clean = False
            if bridge is not None:
                bridge.close()
            if server is not None:
                server.should_exit = True
            if api_thread is not None:
                api_thread.join(timeout=15)
                cleanup_steps["local_api_stopped"] = not api_thread.is_alive()
                clean &= not api_thread.is_alive()
            if phone_modified:
                try:
                    installed_i028 = f"package:{PACKAGE}" in adb(
                        "shell", "pm", "list", "packages", PACKAGE
                    )
                    if installed_i028 and saved_session():
                        # Only this run's account can exist: preflight refused any saved session.
                        # Cleanup entrypoint uses no network, never reads/prints vault contents.
                        cleared = subprocess.run(  # noqa: S603 - fixed test-only cleanup phase.
                            [
                                str(flutter),
                                "test",
                                "integration_test/i028_live_test.dart",
                                "-d",
                                serial,
                                "--no-pub",
                                "--no-uninstall",
                                "--dart-define=VPN_WINDOW_TUNNELS=true",
                                "--dart-define=VPN_I028_PHASE=cleanup",
                            ],
                            cwd=client,
                            env=env,
                            capture_output=True,
                            timeout=240,
                            check=False,
                        )
                        clean &= cleared.returncode == 0
                        cleanup_steps["phone_session_cleanup_entrypoint"] = cleared.returncode == 0
                        del cleared
                        clean &= not saved_session()
                    cleanup_steps["phone_session_absent"] = (
                        not installed_i028 or not saved_session()
                    )
                    if installed_i028:
                        adb("shell", "am", "force-stop", PACKAGE)
                    if suite == "NaturalSleep":
                        if installed_i028:
                            adb(
                                "shell",
                                "run-as",
                                PACKAGE,
                                "rm",
                                "-f",
                                "no_backup/i028-natural-sleep-stage.txt",
                            )
                            adb(
                                "shell",
                                "run-as",
                                PACKAGE,
                                "rm",
                                "-f",
                                "no_backup/i028-natural-sleep-diagnostics.jsonl",
                            )
                        if f"package:{TEST_PACKAGE}" in adb(
                            "shell", "pm", "list", "packages", TEST_PACKAGE
                        ):
                            adb("uninstall", TEST_PACKAGE)
                    if installed_i028:
                        adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", previous_mode)
                    adb("reverse", "--remove", "tcp:18766")
                    if installed_i028:
                        adb("uninstall", PACKAGE)
                    cleanup_steps["isolated_i028_app_absent"] = PACKAGE not in adb(
                        "shell", "pm", "list", "packages", PACKAGE
                    )
                    adb("uninstall", "org.example.vpnpoc.test")
                    clean &= "tcp:18766" not in adb("reverse", "--list")
                    cleanup_steps["owner_app_untouched"] = "package:org.example.vpnclient" in adb(
                        "shell", "pm", "list", "packages", "org.example.vpnclient"
                    )
                    cleanup_steps["phone_restored"] = (
                        cleanup_steps["isolated_i028_app_absent"]
                        and cleanup_steps["owner_app_untouched"]
                    )
                    clean &= cleanup_steps["phone_restored"]
                except (RuntimeError, subprocess.SubprocessError):
                    cleanup_steps["phone_restored"] = False
                    clean = False
            if instrumentation is not None and instrumentation.poll() is None:
                instrumentation.terminate()
                try:
                    instrumentation.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    instrumentation.kill()
                    instrumentation.wait(timeout=5)
            report["temporary_staging_transport_and_phone_cleanup"] = clean
            report["passed"] &= clean
            covered = {
                "natural sleep and forced Doze": "forced_doze_window_safe_and_real_recovery",
                "Wi-Fi/mobile transition": "wifi_mobile_loss_wifi_real_transfer_and_valid_windows",
                "system VPN permission revoke": (
                    "system_settings_revoke_closes_without_self_restart"
                ),
                "Disconnect during in-flight renewal": (
                    "disconnect_during_real_renewal_response_cannot_reopen"
                ),
            }
            report["open_cases"] = [
                name
                for name in report["open_cases"]
                if report["facts"].get(covered.get(name)) is not True
            ]
            if lifecycle:
                report["open_cases"].append("natural sleep (forced Doze is separate)")
            if not report["passed"]:
                report["failed_at"] = stage
            receipt_name = (
                "i-028-egress-diagnostic.json"
                if suite == "Egress"
                else "i-028-natural-sleep-staging.json"
                if suite == "NaturalSleep"
                else "i-028-network-revoke-staging.json"
                if suite == "NetworkRevoke"
                else "i-028-lifecycle-staging.json"
                if lifecycle
                else "i-028-android-staging.json"
            )
            receipt_path = ROOT / "docs/evidence" / receipt_name
            if receipt_path.exists():
                receipt_path = receipt_path.with_name(
                    f"{receipt_path.stem}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
                )
            receipt_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    assert report["passed"], "i028_acceptance_not_confirmed"

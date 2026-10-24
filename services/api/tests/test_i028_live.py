"""Opt-in I-028: real phone/AWG/staging A, disposable local API/PG; fake Telegram.

Never print raw Flutter, instrumentation, SSH manifest or credential diagnostics.
"""

import asyncio
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import threading
import time
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
PACKAGE = "org.example.vpnclient"
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
    "natural_sleep_window_cutoff_and_real_recovery",
}
PROBE_FLAGS = {
    "active_transport_cellular",
    "active_transport_wifi",
    "application_initialized",
    "process_uid_matches_target",
    "test_internet_permission_declared",
    "ipv4_available",
    "ipv4_used_fallback",
    "ipv6_available",
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


def helper(verb: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
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
        [*SSH, HOST, HELPER + verb], data=json.dumps(value).encode() if value is not None else None
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
        elif name in {"ipv4_failure", "ipv4_primary_failure", "dns_failure"} and re.fullmatch(
            r"none|dns|tls|timeout|connect|permission|io|route|http_[1-5][0-9]{2}", value
        ):
            result[name] = value
    if result.get("probe_complete") is not True:
        raise RuntimeError("i028_probe_incomplete")
    return result


def test_i028_real_android(harness: Harness) -> None:
    h = harness
    suite = os.environ.get("VPN_I028_SUITE", "Main")
    lifecycle = suite != "Main"
    required_facts = (
        NATURAL_SLEEP_FACTS
        if suite == "NaturalSleep"
        else NETWORK_REVOKE_FACTS
        if suite == "NetworkRevoke"
        else LIFECYCLE_FACTS
        if lifecycle
        else FACTS
    )
    adb_exe = Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"
    serial = os.environ["VPN_I028_SERIAL"]
    client = Path(os.environ["VPN_I028_CLIENT_DIR"])
    flutter = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/toolchains/flutter/bin/flutter.bat"

    def adb(*args: str, timeout: int = 90) -> str:
        return command([str(adb_exe), "-s", serial, *args], timeout=timeout).decode(
            "utf-8", "replace"
        )

    def saved_session() -> bool:
        if "no_backup" not in adb("shell", "run-as", PACKAGE, "ls", "-a").splitlines():
            return False
        return "session.vault" in adb("shell", "run-as", PACKAGE, "ls", "no_backup").splitlines()

    assert adb("get-state").strip() == "device"
    assert "package:org.example.vpnpoc.test" not in adb(
        "shell", "pm", "list", "packages", "org.example.vpnpoc.test"
    )
    assert "tcp:18766" not in adb("reverse", "--list")
    assert "package:org.example.vpnpoc" in adb(
        "shell", "pm", "list", "packages", "org.example.vpnpoc"
    )
    # Never erase an existing real account or alter another VPN's always-on policy.
    assert adb("shell", "settings", "get", "secure", "always_on_vpn_app").strip() == "null"
    # dumpsys retains old NetworkCapabilities even after disconnect; the independent
    # probe below checks the actual active network instead of that stale structure.
    assert not saved_session()
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
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "services/api/tests/test_i028_live.py",
                "scripts/run_i028_staging.ps1",
                "scripts/i028_device_scenarios.py",
                "apps/client/integration_test/i028_live_test.dart",
                "apps/android/app/src/androidTest/java/org/example/vpnpoc/I028Probe.java",
                "infra/staging-v3/runtime.py",
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
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job: concurrent.futures.Future[dict[str, Any]] | None = None
    api_thread: threading.Thread | None = None
    tunnel: subprocess.Popen[bytes] | None = None
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
                output = adb(
                    "shell",
                    "am",
                    "instrument",
                    "-w",
                    "-r",
                    "-e",
                    "mode",
                    mode,
                    "org.example.vpnpoc.test/org.example.vpnpoc.I028Probe",
                    timeout=45,
                )
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

            @app.post("/api/v1/__i028__/action/{name}", include_in_schema=False)
            def action(request: Request, name: str) -> dict[str, bool]:
                nonlocal job
                principal(request)
                assert name in {
                    "probe_all",
                    "probe_ipv4",
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
                assert value[
                    "name"
                ] in FACTS | LIFECYCLE_FACTS | NETWORK_REVOKE_FACTS | NATURAL_SLEEP_FACTS | {
                    "baseline_ipv6_available_included_ipv6_blocked",
                    "direct_ipv4_comparison_unavailable",
                    "native_reports_fresh_handshake",
                    "mobile_direct_ipv6_baseline_available",
                }
                report["facts"][value["name"]] = True
                return {"accepted": True}

            @app.post("/api/v1/__i028__/renewals", include_in_schema=False)
            def renewals(request: Request) -> dict[str, bool]:
                principal(request)
                with Session(h.engine) as session:
                    connection = session.scalar(
                        select(PeerConnection).order_by(PeerConnection.created_at.desc())
                    )
                    assert (
                        connection is not None
                        and connection.generation >= 4
                        and connection.state == "active"
                    )
                    report["sustained_acknowledged_generation"] = connection.generation
                return {"accepted": True}

            @app.post("/api/v1/__i028__/native_observation", include_in_schema=False)
            def native_observation(request: Request, value: dict[str, Any]) -> dict[str, bool]:
                principal(request)
                assert set(value) == {"up", "network", "handshake_age", "remaining_ms"}
                assert type(value["up"]) is bool and type(value["network"]) is bool
                assert type(value["handshake_age"]) is int and -1 <= value["handshake_age"] <= 86400
                assert type(value["remaining_ms"]) is int and 0 <= value["remaining_ms"] <= 90000
                report["last_sustained_native_observation"] = value
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
            adb(
                "install",
                "-r",
                os.environ["VPN_I028_PROBE_APK"],
            )
            adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "allow")
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
            phase_name = (
                "natural_sleep"
                if suite == "NaturalSleep"
                else "network_revoke"
                if suite == "NetworkRevoke"
                else "lifecycle"
                if lifecycle
                else "run"
            )
            for phase in ("seed", phase_name):
                stage = "flutter_" + phase
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
                success = result.returncode == 0
                del result
                # Pytest assertion rewriting must never render CompletedProcess,
                # whose captured output could contain a live test credential.
                if not success:
                    raise RuntimeError("i028_flutter_failed_see_sanitized_receipt")
                if phase == "seed":
                    assert (
                        "session.vault"
                        in adb("shell", "run-as", PACKAGE, "ls", "no_backup").splitlines()
                    )
                    adb("shell", "am", "force-stop", PACKAGE)
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
                    if saved_session():
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
                    cleanup_steps["phone_session_absent"] = not saved_session()
                    adb("shell", "am", "force-stop", PACKAGE)
                    adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", previous_mode)
                    adb("reverse", "--remove", "tcp:18766")
                    adb(
                        "install",
                        "-r",
                        str(ROOT / "apps/client/build/app/outputs/flutter-apk/app-debug.apk"),
                    )
                    adb("uninstall", "org.example.vpnpoc.test")
                    clean &= "tcp:18766" not in adb("reverse", "--list")
                    cleanup_steps["phone_restored"] = True
                except (RuntimeError, subprocess.SubprocessError):
                    cleanup_steps["phone_restored"] = False
                    clean = False
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
                "i-028-natural-sleep-staging.json"
                if suite == "NaturalSleep"
                else "i-028-network-revoke-staging.json"
                if suite == "NetworkRevoke"
                else "i-028-lifecycle-staging.json"
                if lifecycle
                else "i-028-android-staging.json"
            )
            (ROOT / "docs/evidence" / receipt_name).write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    assert report["passed"], "i028_acceptance_not_confirmed"

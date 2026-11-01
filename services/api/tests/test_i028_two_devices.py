"""Opt-in two-physical-device I-028 acceptance against bounded staging A."""

from __future__ import annotations

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
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import pytest
import uvicorn
from fastapi import Request as ApiRequest
from fastapi import Response
from scripts.i028_two_device_driver import (
    Role,
    TwoDeviceCoordinator,
    android_install_hash_matches,
    android_install_user_restriction_retryable,
    cleanup_owned_packages,
    exactly_two_physical_serials,
    i028_cleanup_steps_passed,
    parse_i028_postcheck,
    probe_confirms_staging_vpn,
    retry_bounded_cleanup,
    retry_bounded_readonly,
    safe_android_install_failure_categories,
    safe_cleanup_failure_code,
    safe_flutter_failure_categories,
    safe_flutter_failure_details,
    safe_i028_flutter_markers,
    safe_probe_failure_code,
    two_device_flutter_test_args,
    two_device_start_barrier_ready,
    unexpected_flutter_exit_roles,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from test_authentication import Harness, harness, issue  # noqa: F401
from test_quota import event

from vpn_api.auth.contracts import StartResponse
from vpn_api.auth.security import Principal
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    Device,
    LoginAttempt,
    PeerConnection,
    PeerWindowHold,
    PeerWindowReceipt,
    SessionFamily,
    VPNNode,
)
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeRegistry
from vpn_api.quota.service import LedgerService

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "org.example.vpnclient.i028"
TEST_PACKAGE = PACKAGE + ".test"
PROBE_PACKAGE = "org.example.vpnpoc.i028"
PROBE_TEST_PACKAGE = PROBE_PACKAGE + ".test"
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
APPROVED = os.environ.get("VPN_I028_STAGING_APPROVED") == "45.10.166.106"
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not APPROVED, reason="bounded I-028 staging not requested"),
]


def _fixed(
    parts: list[str],
    *,
    timeout: int = 60,
    data: bytes | None = None,
    failure_code: str = "i028_two_device_fixed_command_failed",
) -> bytes:
    result = subprocess.run(  # noqa: S603
        parts, input=data, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(failure_code)
    return result.stdout


def _helper(
    verb: str,
    value: dict[str, Any] | None = None,
    *,
    control_path: str | None = None,
) -> dict[str, Any]:
    if verb not in {"status", "manifest", "configure", "start-agent", "stop-agent", "stop"}:
        raise ValueError("i028_two_device_helper_verb_invalid")
    ssh = [*SSH]
    if control_path is not None:
        if not re.fullmatch(
            r"/tmp/dv-i028-[A-Za-z0-9]+/master",  # noqa: S108 - validated private socket path.
            control_path,
        ):
            raise ValueError("i028_two_device_control_path_invalid")
        ssh.extend(["-S", control_path])
    raw = _fixed(
        [*ssh, HOST, HELPER + verb],
        timeout=30,
        data=json.dumps(value).encode("utf-8") if value is not None else None,
        failure_code=f"i028_two_device_helper_{verb}_failed",
    )
    # Only fixed helper JSON crosses this boundary; callers never log it raw.
    return cast(dict[str, Any], json.loads(raw))


def _api_request(method: str, path: str, token: str) -> tuple[int, bytes]:
    request = Request(
        "http://127.0.0.1:18766" + path,
        method=method,
        headers={"Authorization": token},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed loopback API.
            return response.status, response.read(65536)
    except HTTPError as exc:
        return exc.code, exc.read(4096)


def test_i028_two_physical_devices(harness: Harness) -> None:  # noqa: F811
    """Two real Android processes, two real identities and staging slots; TEST OTP only."""
    h = harness
    adb_exe = Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"
    flutter = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/toolchains/flutter/bin/flutter.bat"
    client = Path(os.environ["VPN_I028_CLIENT_DIR"])
    artifacts = json.loads((ROOT / ".cache/i028-ready.json").read_text(encoding="utf-8"))
    serials = retry_bounded_readonly(
        lambda: exactly_two_physical_serials(
            _fixed([str(adb_exe), "devices", "-l"]).decode("utf-8", "replace").splitlines()[1:]
        )
    )
    coordinator = TwoDeviceCoordinator()
    role_for_user: dict[Any, Role] = {}
    role_tokens: dict[str, str] = {}
    role_commands = {"a": "wait", "b": "wait"}
    role_done = {"a": threading.Event(), "b": threading.Event()}
    role_seeded = {"a": threading.Event(), "b": threading.Event()}
    role_appop_ready = {"a": threading.Event(), "b": threading.Event()}
    role_native_ready = {"a": threading.Event(), "b": threading.Event()}
    report: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "scope": "two physical Android devices, disposable local API/PostgreSQL, bounded staging A",
        "telegram": "TEST FAKE ONLY",
        "quota_funding": "TEST FREE GRANT ONLY",
        "facts": {},
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "services/api/tests/test_i028_two_devices.py",
                "services/api/tests/test_i028_two_device_driver.py",
                "scripts/i028_two_device_driver.py",
                "scripts/i028_two_device_preflight.py",
                "scripts/run_i028_staging.ps1",
                "apps/client/integration_test/i028_two_device_test.dart",
                "infra/staging-v3/runtime.py",
            )
        },
        "devices": [],
        "cleanup_steps": {},
        "open_cases": ["independent provider failure", "comprehensive DNS/IPv6 packet capture"],
        "vpn_dataplane_acceptance": False,
    }
    device_owned: list[dict[str, Any]] = []
    for serial in serials:

        def adb(*args: str, timeout: int = 60, _serial: str = serial) -> str:
            return _fixed([str(adb_exe), "-s", _serial, *args], timeout=timeout).decode(
                "utf-8", "replace"
            )

        packages = set(adb("shell", "pm", "list", "packages").splitlines())
        if f"package:{PACKAGE}" in packages or f"package:{TEST_PACKAGE}" in packages:
            raise RuntimeError("preexisting_i028_application_preserved")
        if f"package:{PROBE_TEST_PACKAGE}" in packages:
            raise RuntimeError("preexisting_probe_test_package_preserved")
        if "tcp:18766" in adb("reverse", "--list"):
            raise RuntimeError("preexisting_adb_reverse_preserved")
        if adb("shell", "settings", "get", "secure", "always_on_vpn_app").strip() not in {
            "null",
            "",
        }:
            raise RuntimeError("preexisting_always_on_policy_preserved")
        if adb("shell", "settings", "get", "secure", "always_on_vpn_lockdown").strip() not in {
            "null",
            "0",
            "false",
            "",
        }:
            raise RuntimeError("preexisting_lockdown_policy_preserved")
        device_owned.append(
            {
                "serial": serial,
                "initial_packages": packages,
                "previous_appop": "default",
                "reverse_added": False,
                "probe_base_added": f"package:{PROBE_PACKAGE}" not in packages,
                "probe_test_added": False,
                "client_added": False,
            }
        )

    h.settings.enable_window_tunnels = True
    h.settings.tunnel_profile_file = None
    state: dict[str, Any] = {"roles": {}, "commands": role_commands}
    api_thread: threading.Thread | None = None
    server: uvicorn.Server | None = None
    tunnel: subprocess.Popen[bytes] | None = None
    bridge: Any = None
    agent_started = False
    tunnel_control_dir: str | None = None
    tunnel_control_path: str | None = None
    command_processes: list[subprocess.Popen[bytes]] = []
    command_log_handles: list[Any] = []
    command_log_paths: list[Path] = []

    def close_reverse_tunnel() -> None:
        nonlocal tunnel, tunnel_control_dir
        if tunnel is not None:
            if tunnel_control_path is not None:
                try:
                    _fixed(
                        [*SSH, "-S", tunnel_control_path, "-O", "exit", HOST],
                        timeout=15,
                    )
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
            if tunnel.poll() is None:
                tunnel.terminate()
            try:
                tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)
            tunnel = None
        if tunnel_control_dir is not None:
            _fixed(
                ["wsl.exe", "-d", "kali-linux", "--", "rmdir", "--", tunnel_control_dir],
                timeout=10,
            )
            tunnel_control_dir = None
        report["cleanup_steps"]["local_reverse_tunnel_closed"] = True

    with tempfile.TemporaryDirectory(prefix="i028-two-") as directory:
        private = Path(directory)
        try:
            owner = _fixed(["whoami.exe"]).decode().strip()
            _fixed(
                [
                    "icacls.exe",
                    str(private),
                    "/inheritance:r",
                    "/grant:r",
                    owner + ":(OI)(CI)F",
                    "*S-1-5-18:(OI)(CI)F",
                ]
            )
            provisioned = NodeRegistry(h.engine).provision(
                ProvisionNodeInput(
                    idempotency_key=uuid4(), name="I028 two-device TEST", region="staging"
                )
            )
            if provisioned.secret is None:
                raise RuntimeError("i028_two_device_node_credential_missing")
            manifest = {
                "version": 3,
                "nodes": [{"node_id": str(provisioned.node_id), **_helper("manifest")}],
            }
            manifest_path = private / "windows.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            h.settings.window_manifest_file = manifest_path
            _helper(
                "configure", {"node_id": str(provisioned.node_id), "credential": provisioned.secret}
            )
            app = create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender)

            def principal(request: ApiRequest) -> tuple[Principal, str | None]:
                token = request.headers.get("Authorization", "")
                with transaction(h.engine) as session:
                    value = h.auth.authenticate(
                        session, token.removeprefix("Bearer "), "tunnels:manage"
                    )
                return value, role_for_user.get(value.user_id)

            @app.middleware("http")
            async def test_faults(
                request: ApiRequest, call_next: RequestResponseEndpoint
            ) -> Response:
                return await call_next(request)

            @app.get("/api/v1/__i028__/two-device/role", include_in_schema=False)
            def assigned_role(request: ApiRequest) -> dict[str, str]:
                p, actual = principal(request)
                if actual not in {"a", "b"}:
                    raise ValueError("i028_two_device_role_missing")
                return {"role": actual}

            @app.post("/api/v1/__i028__/two-device/issue/{role}", include_in_schema=False)
            def issue_role(role: str, value: dict[str, str]) -> dict[str, str]:
                if role not in {"a", "b"} or set(value) != {"request_id", "verifier"}:
                    raise ValueError("i028_two_device_issue_request_invalid")
                with Session(h.engine) as session:
                    attempt = session.get(LoginAttempt, UUID(value["request_id"]))
                    if attempt is None or attempt.verifier_digest is None:
                        raise ValueError("i028_two_device_test_attempt_missing")
                    start = StartResponse(
                        request_id=attempt.id,
                        verifier=value["verifier"],
                        bot_url="https://t.me/LocalTestLoginBot",
                        expires_at=attempt.expires_at,
                    )
                return {"code": issue(h, start, subject=2**40 + (7201 if role == "a" else 7202))}

            @app.post("/api/v1/__i028__/two-device/seeded/{role}", include_in_schema=False)
            def seeded(role: str, request: ApiRequest) -> dict[str, bool]:
                p, actual = principal(request)
                if role not in {"a", "b"} or actual is not None:
                    raise ValueError("i028_two_device_seed_identity_conflict")
                role_for_user[p.user_id] = cast(Role, role)
                state["roles"][role] = {"user_id": p.user_id, "family_id": p.family_id}
                LedgerService(h.engine).apply(p.user_id, event("credit", 512 * 1024 * 1024))
                role_seeded[role].set()
                return {"accepted": True}

            @app.get("/api/v1/__i028__/two-device/ready/{role}", include_in_schema=False)
            def ready(role: str, request: ApiRequest) -> dict[str, bool]:
                _principal, actual = principal(request)
                if role not in {"a", "b"} or actual != role or role not in state["roles"]:
                    raise ValueError("i028_two_device_seed_barrier_identity_mismatch")
                return {
                    "ready": two_device_start_barrier_ready(
                        state["roles"],
                        [
                            item_role
                            for item_role, item in role_appop_ready.items()
                            if item.is_set()
                        ],
                    )
                }

            @app.post("/api/v1/__i028__/two-device/restored/{role}", include_in_schema=False)
            def restored(role: str, request: ApiRequest, value: dict[str, str]) -> dict[str, bool]:
                p, actual = principal(request)
                expected = state["roles"].get(role)
                if (
                    actual != role
                    or expected is None
                    or p.user_id != expected["user_id"]
                    or value.get("user_id") != str(p.user_id)
                ):
                    raise ValueError("i028_two_device_restore_identity_mismatch")
                return {"accepted": True}

            @app.get("/api/v1/__i028__/two-device/identity/{role}", include_in_schema=False)
            def identity(role: str, request: ApiRequest) -> dict[str, str]:
                p, actual = principal(request)
                if actual != role:
                    raise ValueError("i028_two_device_identity_mismatch")
                with Session(h.engine) as session:
                    family = session.get(SessionFamily, p.family_id)
                    if family is None or family.device_id is None:
                        raise ValueError("i028_two_device_device_missing")
                    return {"family_id": str(p.family_id), "device_id": str(family.device_id)}

            @app.post("/api/v1/__i028__/two-device/native-ready/{role}", include_in_schema=False)
            def native_ready(role: str, request: ApiRequest) -> dict[str, bool]:
                p, actual = principal(request)
                expected = state["roles"].get(role)
                if (
                    role not in {"a", "b"}
                    or actual != role
                    or expected is None
                    or p.user_id != expected["user_id"]
                ):
                    raise ValueError("i028_two_device_native_ready_identity_mismatch")
                role_native_ready[role].set()
                return {"accepted": True}

            @app.post("/api/v1/__i028__/two-device/connected/{role}", include_in_schema=False)
            def connected(role: str, request: ApiRequest, value: dict[str, Any]) -> dict[str, bool]:
                p, actual = principal(request)
                if actual != role or role not in {"a", "b"}:
                    raise ValueError("i028_two_device_connection_role_mismatch")
                with Session(h.engine) as session:
                    row = session.get(PeerConnection, UUID(value.get("connection_id", "")))
                    family = session.get(SessionFamily, p.family_id)
                    device = session.get(Device, row.device_id) if row is not None else None
                    if (
                        row is None
                        or family is None
                        or device is None
                        or row.user_id != p.user_id
                        or row.device_id != family.device_id
                    ):
                        raise ValueError("i028_two_device_connection_owner_mismatch")
                    hold = session.get(PeerWindowHold, row.window_id)
                    if (
                        hold is None
                        or str(hold.grant["window_id"]) != value.get("window_id")
                        or row.slot_id != value.get("slot_id")
                    ):
                        raise ValueError("i028_two_device_window_mismatch")
                role_tokens[role] = request.headers.get("Authorization", "")
                # Record only IDs; public key/token values never enter the receipt.
                coordinator.bind(
                    cast(Role, role),
                    user_id=str(p.user_id),
                    device_id=str(row.device_id),
                    family_id=str(p.family_id),
                )
                if not coordinator.connected(
                    cast(Role, role),
                    connection_id=str(row.id),
                    window_id=str(value["window_id"]),
                    slot_id=row.slot_id,
                ):
                    report["facts"]["first_device_connected"] = True
                else:
                    report["facts"]["both_devices_connected_distinct_windows_slots"] = True
                    with Session(h.engine) as session:
                        device_a = session.get(
                            Device, UUID(coordinator.records["a"].device_id or "")
                        )
                        device_b = session.get(
                            Device, UUID(coordinator.records["b"].device_id or "")
                        )
                        if (
                            device_a is None
                            or device_b is None
                            or device_a.public_key == device_b.public_key
                        ):
                            raise ValueError("i028_two_device_public_keys_not_distinct")
                    report["facts"]["device_public_keys_distinct"] = True
                return {"accepted": True}

            @app.get("/api/v1/__i028__/two-device/command/{role}", include_in_schema=False)
            def get_command(role: str, request: ApiRequest) -> dict[str, str]:
                _p, actual = principal(request)
                if actual != role:
                    raise ValueError("i028_two_device_command_role_mismatch")
                return {"command": role_commands[role]}

            @app.post("/api/v1/__i028__/two-device/disconnected/{role}", include_in_schema=False)
            def disconnected(role: str, request: ApiRequest) -> dict[str, bool]:
                _p, actual = principal(request)
                if actual != role:
                    raise ValueError("i028_two_device_disconnect_role_mismatch")
                coordinator.disconnected(cast(Role, role))
                role_done[role].set()
                report["facts"][f"device_{role}_disconnected"] = True
                return {"accepted": True}

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
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.1)
            if not server.started:
                raise RuntimeError("i028_two_device_api_start_failed")
            spec = importlib.util.spec_from_file_location(
                "i028_two_device_pipe", ROOT / "scripts/i028_pipe_relay.py"
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("i028_two_device_relay_missing")
            relay_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(relay_module)
            bridge = relay_module.PipeRelay(ROOT / "scripts/i028_pipe_relay.py")
            tunnel_control_dir = _fixed(
                [
                    "wsl.exe",
                    "-d",
                    "kali-linux",
                    "--",
                    "mktemp",
                    "-d",
                    "-p",
                    "/tmp",  # noqa: S108 - mktemp atomically creates a private 0700 directory.
                    "dv-i028-XXXXXX",
                ],
                timeout=10,
            ).decode("ascii", "strict").strip()
            if not re.fullmatch(
                r"/tmp/dv-i028-[A-Za-z0-9]+",  # noqa: S108 - validates the private runtime path.
                tunnel_control_dir,
            ):
                raise RuntimeError("i028_two_device_tunnel_control_path_invalid")
            control_mode = _fixed(
                ["wsl.exe", "-d", "kali-linux", "--", "stat", "-c", "%a", "--", tunnel_control_dir],
                timeout=10,
            ).decode("ascii", "strict").strip()
            if control_mode != "700":
                raise RuntimeError("i028_two_device_tunnel_control_mode_invalid")
            tunnel_control_path = tunnel_control_dir + "/master"
            tunnel = subprocess.Popen(  # noqa: S603 - fixed scoped SSH reverse tunnel.
                [
                    *SSH,
                    "-N",
                    "-T",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-M",
                    "-S",
                    tunnel_control_path,
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
            if tunnel.poll() is not None:
                raise RuntimeError("i028_two_device_reverse_tunnel_failed")
            if tunnel_control_path is None:
                raise RuntimeError("i028_two_device_control_path_missing")

            def helper(verb: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
                return _helper(verb, value, control_path=tunnel_control_path)

            helper("start-agent")
            agent_started = True
            for _ in range(60):
                with Session(h.engine) as session:
                    node = session.get(VPNNode, provisioned.node_id)
                    if node is not None and node.peer_runtime_id is not None:
                        break
                time.sleep(0.5)
            else:
                raise RuntimeError("i028_two_device_agent_not_ready")

            remote = helper("status")
            remaining = remote.get("remaining_seconds")
            report["staging_baseline"] = {
                "prepared": remote.get("prepared") is True,
                "remaining_seconds": remaining
                if isinstance(remaining, int) and not isinstance(remaining, bool)
                else None,
                "slot_count": remote.get("slot_count")
                if isinstance(remote.get("slot_count"), int)
                else None,
                "live_slots": remote.get("live_slots")
                if isinstance(remote.get("live_slots"), list)
                else None,
                "peer_counts": remote.get("peer_counts")
                if isinstance(remote.get("peer_counts"), list)
                else None,
                "agent_active": remote.get("agent_active") is True,
                "cleanup_complete": remote.get("cleanup_complete") is True,
            }
            if (
                remote.get("prepared") is not True
                or remote.get("remaining_seconds", 0) < 1500
                or remote.get("live_slots") != []
                or remote.get("peer_counts") != [0, 0]
            ):
                raise RuntimeError("i028_two_device_staging_window_or_clean_baseline_invalid")
            report["staging_remaining_seconds_at_start"] = remote["remaining_seconds"]

            base_apk = Path(str(artifacts["probe_base_apk"]))
            probe_test_apk = Path(str(artifacts["probe_apk"]))
            expected = {
                "probe_base_sha256": base_apk,
                "probe_test_sha256": probe_test_apk,
            }
            for field, path in expected.items():
                if hashlib.sha256(path.read_bytes()).hexdigest() != artifacts.get(field):
                    raise RuntimeError("i028_two_device_probe_artifact_changed")

            def probe(index: int) -> dict[str, Any]:
                serial = serials[index]
                from test_i028_live import parse_probe

                try:
                    output = retry_bounded_readonly(
                        lambda: _fixed(
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
                                "ipv4",
                                f"{PROBE_TEST_PACKAGE}/org.example.vpnpoc.I028Probe",
                            ],
                            timeout=45,
                            failure_code="i028_two_device_probe_instrumentation_failed",
                        ),
                        attempts=2,
                        delay_seconds=1,
                    )
                    return parse_probe(output.decode("utf-8", "replace"))
                except Exception as exc:
                    return {
                        "probe_complete": False,
                        "probe_failure_code": safe_probe_failure_code(exc),
                    }

            for index, serial in enumerate(serials):
                info = device_owned[index]

                def adb(
                    *args: str,
                    timeout: int = 60,
                    _serial: str = serial,
                ) -> str:
                    failure_code = "i028_two_device_adb_command_failed"
                    if args[:1] == ("install",):
                        is_probe_test = len(args) > 2 and args[2] == str(probe_test_apk)
                        failure_code = (
                            "i028_two_device_probe_test_install_failed"
                            if is_probe_test
                            else "i028_two_device_probe_base_install_failed"
                        )
                    elif args[:3] == ("shell", "appops", "set"):
                        failure_code = "i028_two_device_appop_setup_failed"
                    elif args[:1] == ("reverse",):
                        failure_code = "i028_two_device_reverse_setup_failed"
                    command = [str(adb_exe), "-s", _serial, *args]
                    if args[:1] == ("install",):
                        is_probe_test = len(args) > 2 and args[2] == str(probe_test_apk)
                        package_name = PROBE_TEST_PACKAGE if is_probe_test else PROBE_PACKAGE
                        artifact_field = (
                            "probe_test_sha256" if is_probe_test else "probe_base_sha256"
                        )
                        install_phase = (
                            "probe_test_install" if is_probe_test else "probe_base_install"
                        )
                        try:
                            result = subprocess.run(  # noqa: S603 - fixed APK and ADB target.
                                command, capture_output=True, timeout=max(timeout, 180), check=False
                            )
                        except subprocess.TimeoutExpired:
                            settle_deadline = time.monotonic() + 120
                            while True:
                                installed_hash: str | None = None
                                try:
                                    installed_packages = (
                                        _fixed(
                                            [
                                                str(adb_exe),
                                                "-s",
                                                _serial,
                                                "shell",
                                                "pm",
                                                "list",
                                                "packages",
                                                package_name,
                                            ],
                                            timeout=10,
                                        )
                                        .decode("utf-8", "replace")
                                        .splitlines()
                                    )
                                    if f"package:{package_name}" in installed_packages:
                                        paths = (
                                            _fixed(
                                                [
                                                    str(adb_exe),
                                                    "-s",
                                                    _serial,
                                                    "shell",
                                                    "pm",
                                                    "path",
                                                    package_name,
                                                ],
                                                timeout=10,
                                            )
                                            .decode("utf-8", "replace")
                                            .splitlines()
                                        )
                                        path_match = (
                                            re.fullmatch(
                                                r"package:(/data/app/[A-Za-z0-9_./=+~-]+/base\.apk)",
                                                paths[0],
                                            )
                                            if len(paths) == 1
                                            else None
                                        )
                                        if path_match is None:
                                            raise RuntimeError(
                                                "i028_two_device_owned_package_path_unverified"
                                            )
                                        installed_hash = (
                                            _fixed(
                                                [
                                                    str(adb_exe),
                                                    "-s",
                                                    _serial,
                                                    "shell",
                                                    "sha256sum",
                                                    path_match[1],
                                                ],
                                                timeout=10,
                                            )
                                            .decode("ascii", "replace")
                                            .split()[0]
                                        )
                                except RuntimeError as exc:
                                    if exc.args == (
                                        "i028_two_device_owned_package_path_unverified",
                                    ):
                                        raise
                                    installed_hash = None
                                except (subprocess.TimeoutExpired, IndexError):
                                    installed_hash = None
                                if installed_hash is not None:
                                    if not android_install_hash_matches(
                                        installed_hash, str(artifacts[artifact_field])
                                    ):
                                        report["diagnostics"] = {
                                            "phase": install_phase,
                                            "safe_categories": [
                                                "installed_hash_mismatch_preserved"
                                            ],
                                        }
                                        raise RuntimeError(
                                            "i028_two_device_owned_package_hash_mismatch_preserved"
                                        ) from None
                                    report["diagnostics"] = {
                                        "phase": install_phase,
                                        "safe_categories": [
                                            "install_completed_after_client_timeout"
                                        ],
                                    }
                                    return "install_completed_after_client_timeout"
                                if time.monotonic() >= settle_deadline:
                                    report["diagnostics"] = {
                                        "phase": install_phase,
                                        "safe_categories": ["install_timeout"],
                                    }
                                    raise RuntimeError(failure_code) from None
                                time.sleep(3)
                        if result.returncode:
                            safe_categories = safe_android_install_failure_categories(
                                result.stdout, result.stderr
                            )
                            if android_install_user_restriction_retryable(
                                safe_categories,
                                package_absent=(
                                    f"package:{package_name}"
                                    not in _fixed(
                                        [
                                            str(adb_exe),
                                            "-s",
                                            _serial,
                                            "shell",
                                            "pm",
                                            "list",
                                            "packages",
                                            package_name,
                                        ],
                                        timeout=10,
                                    )
                                    .decode("utf-8", "replace")
                                    .splitlines()
                                ),
                            ):
                                time.sleep(5)
                                result = subprocess.run(  # noqa: S603 - fixed APK and ADB target.
                                    command,
                                    capture_output=True,
                                    timeout=max(timeout, 180),
                                    check=False,
                                )
                                safe_categories = safe_android_install_failure_categories(
                                    result.stdout, result.stderr
                                )
                        if result.returncode:
                            report["diagnostics"] = {
                                "phase": install_phase,
                                "safe_categories": list(safe_categories),
                            }
                            raise RuntimeError(failure_code)
                        return result.stdout.decode("utf-8", "replace")
                    return _fixed(command, timeout=timeout, failure_code=failure_code).decode(
                        "utf-8", "replace"
                    )

                if not info["probe_base_added"]:
                    paths = adb("shell", "pm", "path", PROBE_PACKAGE).splitlines()
                    path_match = (
                        re.fullmatch(r"package:(/data/app/[A-Za-z0-9_./=+~-]+/base\.apk)", paths[0])
                        if len(paths) == 1
                        else None
                    )
                    if path_match is None:
                        raise RuntimeError("i028_two_device_existing_probe_path_unverified")
                    installed_hash = adb("shell", "sha256sum", path_match[1]).split()
                    if not installed_hash or installed_hash[0] != artifacts["probe_base_sha256"]:
                        raise RuntimeError("i028_two_device_existing_probe_hash_mismatch_preserved")
                if info["probe_base_added"]:
                    # Use adb's push-then-install path. The streaming install
                    # stalled on a physical Pixel; this exact hash-bound APK
                    # installed and was removed successfully with this option.
                    adb("install", "--no-streaming", str(base_apk), timeout=90)
                info["probe_test_added"] = True
                adb("install", "--no-streaming", str(probe_test_apk), timeout=90)
                _fixed(
                    [str(adb_exe), "-s", serial, "reverse", "tcp:18766", "tcp:18766"],
                    failure_code="i028_two_device_reverse_setup_failed",
                )
                info["reverse_added"] = True
                info["client_added"] = True
                role: Role = "a" if index == 0 else "b"
                log_path = private / f"flutter-seed-run-{index}.log"
                log_handle = log_path.open("wb", buffering=0)
                command_log_handles.append(log_handle)
                command_log_paths.append(log_path)
                command_processes.append(
                    subprocess.Popen(  # noqa: S603 - fixed Flutter test target and args.
                        two_device_flutter_test_args(
                            str(flutter),
                            serial,
                            phase="seed_run",
                            role=role,
                            package=PACKAGE,
                            verbose=True,
                        ),
                        cwd=client,
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                    )
                )
                seed_deadline = time.monotonic() + 190
                while not role_seeded[role].wait(timeout=0.25):
                    if command_processes[-1].poll() is not None:
                        output = (
                            command_log_paths[-1].read_bytes()
                            if command_log_paths[-1].is_file()
                            else b""
                        )
                        report["diagnostics"] = {
                            "phase": "seed_run",
                            "return_code": command_processes[-1].returncode,
                            "safe_categories": safe_flutter_failure_categories(output),
                            "safe_gradle_details": safe_flutter_failure_details(output),
                            "safe_markers": safe_i028_flutter_markers(output),
                        }
                        raise RuntimeError("i028_two_device_seed_run_failed")
                    if time.monotonic() >= seed_deadline:
                        raise RuntimeError("i028_two_device_seed_run_timeout")
                adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "allow")
                role_appop_ready[role].set()
                report["devices"].append(
                    {
                        "model": adb("shell", "getprop", "ro.product.model").strip(),
                        "android_release": adb(
                            "shell", "getprop", "ro.build.version.release"
                        ).strip(),
                    }
                )

            def record_run_flutter_diagnostics(checkpoint: str) -> None:
                processes = []
                for index, (process, log_path) in enumerate(
                    zip(command_processes, command_log_paths, strict=True)
                ):
                    output = log_path.read_bytes() if log_path.is_file() else b""
                    processes.append(
                        {
                            "process_index": index,
                            "exit_code": process.poll(),
                            "log_present": bool(output),
                            "safe_categories": safe_flutter_failure_categories(output),
                            "safe_gradle_details": safe_flutter_failure_details(output),
                            "safe_markers": safe_i028_flutter_markers(output),
                        }
                    )
                report["run_flutter_diagnostics"] = {
                    "checkpoint": checkpoint,
                    "processes": processes,
                }

            deadline = time.monotonic() + 240
            handshake_probe_attempted = False
            while time.monotonic() < deadline:
                if all(record.connected for record in coordinator.records.values()):
                    break
                if any(process.poll() is not None for process in command_processes):
                    record_run_flutter_diagnostics("connect_barrier_process_exit")
                    raise RuntimeError("i028_two_device_instrumentation_ended_before_barrier")
                if not handshake_probe_attempted and all(
                    event.is_set() for event in role_native_ready.values()
                ):
                    handshake_probe_attempted = True
                    for round_number in (1, 2):
                        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                            probe_results = list(pool.map(probe, (0, 1)))
                        report.setdefault("handshake_kick_probes", []).append(
                            {"round": round_number, "devices": probe_results}
                        )
                        if all(probe_confirms_staging_vpn(value) for value in probe_results):
                            report["facts"]["both_devices_staging_probe_before_barrier"] = True
                            break
                        if round_number == 1:
                            time.sleep(2)
                    # This probe is an early handshake kick. native-ready only
                    # means the interface, lease and underlying network exist;
                    # DNS/egress may not work until the client reports a fresh
                    # handshake. Keep waiting for the connected barrier, then
                    # require the strict independent egress probe below.
                time.sleep(0.5)
            else:
                record_run_flutter_diagnostics("connect_barrier_timeout")
                raise RuntimeError("i028_two_device_connect_barrier_timeout")

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                initial_probes = list(pool.map(probe, (0, 1)))
            # parse_probe emits only allowlisted booleans and coarse failure
            # categories; raw instrumentation output and egress addresses are
            # never persisted in the evidence receipt.
            report["initial_real_egress_probes"] = initial_probes
            if not all(
                item.get("egress_is_staging") is True and item.get("active_network_is_vpn") is True
                for item in initial_probes
            ):
                raise RuntimeError("i028_two_device_initial_real_egress_failed")
            report["facts"]["both_devices_real_staging_egress"] = True
            snapshot = coordinator.snapshot()
            connection_a = snapshot["a"]["connection_id"]
            connection_b = snapshot["b"]["connection_id"]
            if not isinstance(connection_a, str) or not isinstance(connection_b, str):
                raise RuntimeError("i028_two_device_connection_identity_missing")
            with Session(h.engine) as session, session.begin():
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                rows = {
                    str(row.id): row
                    for row in session.scalars(
                        select(PeerConnection)
                        .where(PeerConnection.id.in_([connection_a, connection_b]))
                        .order_by(PeerConnection.id)
                        .with_for_update()
                    )
                }
                if set(rows) != {connection_a, connection_b}:
                    raise RuntimeError("i028_two_device_connection_rows_missing")
                before = {
                    role: {
                        "state": rows[connection_id].state,
                        "generation": rows[connection_id].generation,
                        "window_id": rows[connection_id].window_id,
                    }
                    for role, connection_id in (("a", connection_a), ("b", connection_b))
                }
                if any(value["state"] not in {"active", "pending"} for value in before.values()):
                    raise RuntimeError("i028_two_device_cross_owner_revoke_precondition_invalid")
                code, _body = _api_request(
                    "DELETE",
                    "/api/v1/window-tunnels/" + connection_b,
                    role_tokens["a"],
                )
                session.expire_all()
                rows_after = {
                    str(row.id): row
                    for row in session.scalars(
                        select(PeerConnection)
                        .where(PeerConnection.id.in_([connection_a, connection_b]))
                        .order_by(PeerConnection.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                }
                after = {
                    role: {
                        "state": rows_after[connection_id].state,
                        "generation": rows_after[connection_id].generation,
                        "window_id": rows_after[connection_id].window_id,
                    }
                    for role, connection_id in (("a", connection_a), ("b", connection_b))
                }
                unchanged = {role: before[role] == after[role] for role in ("a", "b")}
                report["cross_owner_revoke"] = {
                    "http_status": code,
                    "state_before": {role: value["state"] for role, value in before.items()},
                    "state_after": {role: value["state"] for role, value in after.items()},
                    "generation_unchanged": {
                        role: unchanged[role]
                        and before[role]["generation"] == after[role]["generation"]
                        for role in ("a", "b")
                    },
                    "window_unchanged": {
                        role: unchanged[role]
                        and before[role]["window_id"] == after[role]["window_id"]
                        for role in ("a", "b")
                    },
                }
                if code != 404:
                    raise RuntimeError("i028_two_device_cross_owner_revoke_not_denied")
                if not all(unchanged.values()):
                    raise RuntimeError("i028_two_device_foreign_revoke_changed_b")
            report["facts"]["a_foreign_revoke_denied_b_unchanged"] = True
            before_b = probe(1)
            if before_b.get("egress_is_staging") is not True:
                raise RuntimeError("i028_two_device_b_egress_after_foreign_revoke_failed")

            role_commands["a"] = "disconnect"
            disconnect_deadline = time.monotonic() + 90
            while not role_done["a"].wait(0.5):
                ended = [process.poll() for process in command_processes]
                if any(code is not None for code in ended):
                    record_run_flutter_diagnostics("a_disconnect_process_exit")
                    raise RuntimeError("i028_two_device_flutter_run_exited_before_a_disconnect")
                if time.monotonic() >= disconnect_deadline:
                    record_run_flutter_diagnostics("a_disconnect_timeout")
                    raise RuntimeError("i028_two_device_a_disconnect_timeout")
            with Session(h.engine) as session:
                rows = {str(row.id): row for row in session.scalars(select(PeerConnection))}
                if rows[connection_a].state != "closed" or rows[connection_b].state not in {
                    "active",
                    "pending",
                }:
                    raise RuntimeError("i028_two_device_a_close_b_state_invalid")
                report["b_state_after_a_disconnect"] = rows[connection_b].state
            after_a = probe(1)
            if after_a.get("egress_is_staging") is not True:
                raise RuntimeError("i028_two_device_b_stopped_after_a_disconnect")
            report["facts"]["b_continues_after_a_disconnect"] = True
            role_commands["b"] = "disconnect"
            disconnect_deadline = time.monotonic() + 90
            while not role_done["b"].wait(0.5):
                unexpected_exits = unexpected_flutter_exit_roles(
                    tuple(process.poll() for process in command_processes),
                    completed_roles=frozenset({"a"}),
                )
                if unexpected_exits:
                    record_run_flutter_diagnostics("b_disconnect_process_exit")
                    raise RuntimeError("i028_two_device_flutter_run_exited_before_b_disconnect")
                if time.monotonic() >= disconnect_deadline:
                    record_run_flutter_diagnostics("b_disconnect_timeout")
                    raise RuntimeError("i028_two_device_b_disconnect_timeout")

            for process in command_processes:
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    raise RuntimeError("i028_two_device_flutter_process_timeout") from None
                if process.returncode != 0:
                    raise RuntimeError("i028_two_device_flutter_process_failed")

            with Session(h.engine) as session:
                users = [state["roles"][role]["user_id"] for role in ("a", "b")]
                summaries = {}
                for account_role, user_id in zip(("a", "b"), users, strict=True):
                    quota = LedgerService(h.engine).quota(session, user_id)
                    reconciled = LedgerService(h.engine).reconcile(session, user_id)
                    receipts = list(
                        session.scalars(
                            select(PeerWindowReceipt).where(PeerWindowReceipt.user_id == user_id)
                        )
                    )
                    holds = list(
                        session.scalars(
                            select(PeerWindowHold).where(PeerWindowHold.user_id == user_id)
                        )
                    )
                    settled_windows = {row.window_id for row in receipts}
                    unsettled_holds = [row for row in holds if row.id not in settled_windows]
                    unknown_receipts = sum(row.observation == "unknown" for row in receipts)
                    open_count = session.scalar(
                        select(func.count())
                        .select_from(PeerConnection)
                        .where(PeerConnection.user_id == user_id, PeerConnection.state != "closed")
                    )
                    measured = sum(row.observed_bytes or 0 for row in receipts)
                    if (
                        not receipts
                        or any(row.observation != "measured" for row in receipts)
                        or unknown_receipts
                        or unsettled_holds
                        or open_count
                        or quota.reserved_bytes != 0
                        or quota.used_bytes != measured
                        or not reconciled.consistent
                        or reconciled.violations
                    ):
                        raise RuntimeError("i028_two_device_accounting_or_settlement_failed")
                    summaries[account_role] = {
                        "measured_bytes": str(measured),
                        "receipt_count": len(receipts),
                        "reserved_bytes": "0",
                        "unknown_receipts": unknown_receipts,
                        "unsettled_holds": len(unsettled_holds),
                        "open_connections": 0,
                    }
                report["settlement"] = summaries
            report["facts"]["both_accounts_exact_settlement"] = True
            report["passed"] = True
        except Exception as exc:
            reason = str(getattr(exc, "args", ["i028_two_device_failed"])[0])
            report["failed_at"] = (
                reason if re.fullmatch(r"[a-z0-9_]{1,96}", reason) else "i028_two_device_failed"
            )
        finally:
            role_commands.update({"a": "disconnect", "b": "disconnect"})
            for process in command_processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            for log_handle in command_log_handles:
                log_handle.close()
            if agent_started:
                cleanup_failure_stage = "staging_stop"
                try:
                    with transaction(h.engine) as session:
                        for row in session.scalars(
                            select(PeerConnection).where(PeerConnection.state != "closed")
                        ):
                            row.state = "revoke_pending"

                    def stop_staging() -> dict[str, Any]:
                        helper("stop-agent")
                        return helper("stop")

                    stopped = retry_bounded_cleanup(stop_staging, attempts=5, delay_seconds=10)
                    report["cleanup_steps"]["staging_a_closed"] = (
                        stopped.get("cleanup_complete") is True
                        and stopped.get("timeout_timer_disarmed") is True
                        and stopped.get("journal_preserved") is True
                        and stopped.get("temporary_grant_removed") is True
                        and stopped.get("gate_close_errors_before_core_stop") == 0
                    )
                    if not report["cleanup_steps"]["staging_a_closed"]:
                        raise RuntimeError("i028_staging_stop_contract_failed")
                    # The independent host snapshot must not see the runner's
                    # intentional SSH reverse forward on 127.0.0.1:18443.
                    cleanup_failure_stage = "independent_remote_postcheck"
                    close_reverse_tunnel()

                    def checked_postcheck() -> bytes:
                        postcheck_output = _fixed(
                            [
                                *SSH,
                                HOST,
                                "systemctl show --property=ActiveState --value "
                                "detourvpn-i028-timeout.timer && ip netns list && "
                                "ip -o link show && ss -H -lun && ss -H -ltn",
                            ],
                            timeout=20,
                        )
                        timer_state, present_markers = parse_i028_postcheck(postcheck_output)
                        report["cleanup_steps"]["postcheck_timer_state"] = timer_state
                        report["cleanup_steps"]["postcheck_resource_markers"] = list(
                            present_markers
                        )
                        if timer_state != "inactive" or present_markers:
                            raise RuntimeError("i028_independent_postcheck_failed")
                        return postcheck_output

                    retry_bounded_cleanup(checked_postcheck, attempts=5, delay_seconds=10)
                    report["cleanup_steps"]["independent_remote_postcheck"] = True
                except Exception as exc:
                    report["cleanup_steps"]["cleanup_failure_stage"] = cleanup_failure_stage
                    report["cleanup_steps"]["cleanup_failure_code"] = safe_cleanup_failure_code(exc)
                    if cleanup_failure_stage == "staging_stop":
                        report["cleanup_steps"]["staging_a_closed"] = False
                    else:
                        report["cleanup_steps"]["independent_remote_postcheck"] = False
                    report["passed"] = False
            if tunnel is not None or tunnel_control_dir is not None:
                try:
                    close_reverse_tunnel()
                except Exception:
                    report["cleanup_steps"]["local_reverse_tunnel_closed"] = False
                    report["passed"] = False
            if bridge is not None:
                bridge.close()
            if server is not None:
                server.should_exit = True
            if api_thread is not None:
                api_thread.join(timeout=15)
                report["cleanup_steps"]["local_api_stopped"] = not api_thread.is_alive()
            for index, serial in enumerate(serials):
                info = device_owned[index]
                adb = lambda *args, timeout=20, _serial=serial: _fixed(  # noqa: E731
                    [str(adb_exe), "-s", _serial, *args], timeout=timeout
                ).decode("utf-8", "replace")
                try:
                    if (
                        info["client_added"]
                        and f"package:{PACKAGE}"
                        in adb("shell", "pm", "list", "packages").splitlines()
                    ):
                        subprocess.run(  # noqa: S603 - fixed Flutter cleanup entrypoint.
                            two_device_flutter_test_args(
                                str(flutter),
                                serial,
                                phase="cleanup",
                                role="a" if index == 0 else "b",
                                package=PACKAGE,
                            ),
                            cwd=client,
                            capture_output=True,
                            timeout=180,
                            check=False,
                        )
                    current = set(adb("shell", "pm", "list", "packages").splitlines())
                    for package_name, artifact_path, created in (
                        (PROBE_PACKAGE, base_apk, info["probe_base_added"]),
                        (PROBE_TEST_PACKAGE, probe_test_apk, info["probe_test_added"]),
                    ):
                        if created and f"package:{package_name}" in current:
                            paths = adb("shell", "pm", "path", package_name).splitlines()
                            match = (
                                re.fullmatch(
                                    r"package:(/data/app/[A-Za-z0-9_./=+~-]+/base\.apk)",
                                    paths[0],
                                )
                                if len(paths) == 1
                                else None
                            )
                            if match is None:
                                raise RuntimeError("i028_two_device_owned_package_path_unverified")
                            installed = adb("shell", "sha256sum", match[1]).split()
                            if (
                                not installed
                                or installed[0]
                                != hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                            ):
                                raise RuntimeError(
                                    "i028_two_device_owned_package_hash_mismatch_preserved"
                                )
                    owned_names: set[str] = set()
                    if info["client_added"]:
                        owned_names.update({f"package:{PACKAGE}", f"package:{TEST_PACKAGE}"})
                    if info["probe_test_added"]:
                        owned_names.add(f"package:{PROBE_TEST_PACKAGE}")
                    removals = cleanup_owned_packages(
                        initial_packages=info["initial_packages"],
                        current_packages=current,
                        owned_names=owned_names & current,
                    )
                    for package_name in removals:
                        if package_name == f"package:{PACKAGE}":
                            adb(
                                "shell",
                                "appops",
                                "set",
                                PACKAGE,
                                "ACTIVATE_VPN",
                                info["previous_appop"],
                            )
                        adb("uninstall", package_name.removeprefix("package:"))
                    if info["probe_base_added"] and f"package:{PROBE_PACKAGE}" in current:
                        adb("uninstall", PROBE_PACKAGE)
                    if info["reverse_added"]:
                        adb("reverse", "--remove", "tcp:18766")
                    final_packages = set(adb("shell", "pm", "list", "packages").splitlines())
                    report["cleanup_steps"][f"device_{index}_restored"] = (
                        f"package:{PACKAGE}" not in final_packages
                        and f"package:{TEST_PACKAGE}" not in final_packages
                        and f"package:{PROBE_TEST_PACKAGE}" not in final_packages
                        and (
                            "package:" + PROBE_PACKAGE in info["initial_packages"]
                            or f"package:{PROBE_PACKAGE}" not in final_packages
                        )
                        and ("tcp:18766" not in adb("reverse", "--list"))
                    )
                except Exception:
                    report["cleanup_steps"][f"device_{index}_restored"] = False
            report["passed"] = report["passed"] and i028_cleanup_steps_passed(
                report["cleanup_steps"]
            )
            receipt = ROOT / "docs/evidence/i-028-two-device-staging.json"
            if receipt.exists():
                receipt = receipt.with_name(
                    f"i-028-two-device-staging-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
                )
            receipt.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    assert report["passed"], "I-028 two-device run failed; inspect sanitized receipt"

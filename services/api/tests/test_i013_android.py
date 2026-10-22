"""Physical Flutter + real API/PostgreSQL; explicitly FAKE Telegram delivery.

Local mode never starts an endpoint. Live mode needs a separately approved
bounded staging grant and reuses the reviewed I-012 controller unchanged.
No test routes or credentials are included in production API/APKs.
"""

import base64
import json
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4, uuid5

import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_authentication import Harness, harness, issue
from test_i012_live import PRIVATE, ROOT, adb, remote, transport

from vpn_api.auth.contracts import StartResponse
from vpn_api.db.models import (
    LoginAttempt,
    QuotaLedger,
    RewardClaim,
    RewardRule,
    Tunnel,
    TunnelReservation,
    VPNNode,
)
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeRegistry
from vpn_api.quota.contracts import LedgerCommand
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.admission import NodeProfile

__all__ = ["harness"]
LIVE = os.environ.get("VPN_I013_STAGING_APPROVED") == "45.10.166.106"
QUOTA = os.environ.get("VPN_I014_LOCAL_ANDROID") == "1"
REWARDS = os.environ.get("VPN_I015_LOCAL_ANDROID") == "1"
ENABLED = LIVE or QUOTA or REWARDS or os.environ.get("VPN_I013_LOCAL_ANDROID") == "1"
PACKAGE = "org.example.vpnclient"
FACTS = {
    "flutter_auth_ui",
    "admission_closed_no_false_connected",
    "flutter_handshake_and_staging_egress",
    "background_foreground_preserves_lease",
    "disconnect_restores_network",
    "remote_revoke_stops_flutter_vpn",
    "lease_expiry_stops_flutter_vpn",
}
if QUOTA:
    FACTS.add("quota_ui_exact_and_paginated")
if REWARDS:
    FACTS.add("reward_history_and_replay")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not ENABLED, reason="I-013 physical run not requested"),
]


def test_i013_android_client(harness: Harness) -> None:
    assert not ((QUOTA or REWARDS) and LIVE), "Quota/rewards acceptance is local only"
    assert not (QUOTA and REWARDS), "Run one issue's physical acceptance at a time"
    h = harness
    h.settings.initial_quota_bytes = 16777216
    h.settings.tunnel_profile_file = None  # Local mode cannot inherit an endpoint profile.
    report: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "live_staging": LIVE,
        "real_postgresql": True,
        "real_api": True,
        "telegram_delivery": "TEST FAKE ONLY",
        "facts": {},
        "passed": False,
    }
    app = create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender)

    if REWARDS:
        # Synthetic configuration only in disposable PostgreSQL; real public claim API.
        with Session(h.engine) as session, session.begin():
            now = datetime.now(UTC)
            reward_rule = RewardRule(
                reward_type="daily",
                reward_bytes=17,
                daily_cap_bytes=17,
                daily_cap_claims=1,
                cooldown_seconds=1,
                enabled=True,
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=1),
            )
            session.add(reward_rule)
            session.flush()
            reward_rule_id = str(reward_rule.id)
        reward_key = str(uuid4())

        @app.post("/api/v1/__i015__/config", include_in_schema=False)
        def test_reward_config() -> dict[str, str]:
            return {"rule_id": reward_rule_id, "idempotency_key": reward_key}

        @app.post("/api/v1/__i015__/verify", include_in_schema=False)
        def test_reward_verify(request: Request) -> dict[str, int]:
            with Session(h.engine) as session:
                principal = app.state.auth.authenticate(
                    session,
                    request.headers.get("Authorization", "").removeprefix("Bearer "),
                    "quota:read",
                )
                claims = list(
                    session.scalars(
                        select(RewardClaim).where(RewardClaim.user_id == principal.user_id)
                    )
                )
                rows = list(
                    session.scalars(
                        select(QuotaLedger).where(QuotaLedger.user_id == principal.user_id)
                    )
                )
                reward_rows = [row for row in rows if row.source_type == "reward"]
                assert len(claims) == len(reward_rows) == 1
                assert reward_rows[0].source_id == claims[0].id
                assert reward_rows[0].delta_bytes == claims[0].reward_bytes == 17
                ledger = LedgerService(h.engine)
                quota = ledger.quota(session, principal.user_id)
                assert quota.available_bytes == sum(row.delta_bytes for row in rows)
                assert quota.available_bytes == h.settings.initial_quota_bytes + 17
                assert quota.reserved_bytes == 0 and quota.revision == len(rows) == 2
                assert ledger.reconcile(session, principal.user_id).consistent
                report["reward_database_consistent"] = True
                return {"available_bytes": quota.available_bytes, "revision": quota.revision}

    if QUOTA:

        @app.post("/api/v1/__i014__/seed", include_in_schema=False)
        def test_quota_seed(request: Request) -> dict[str, int]:
            # TEST ONLY synthetic credits, deterministic replay keys; no tunnel traffic.
            with Session(h.engine) as session:
                principal = app.state.auth.authenticate(
                    session,
                    request.headers.get("Authorization", "").removeprefix("Bearer "),
                    "quota:read",
                )
            ledger = LedgerService(h.engine)
            for index in range(24):
                key = uuid5(principal.user_id, f"i014-test-credit-{index}")
                ledger.apply(
                    principal.user_id,
                    LedgerCommand(
                        kind="credit",
                        amount_bytes=9007199254740993 if index == 0 else 1,
                        source_id=key,
                        idempotency_key=key,
                    ),
                )
            with Session(h.engine) as session:
                rows = list(
                    session.scalars(
                        select(QuotaLedger).where(
                            QuotaLedger.user_id == principal.user_id,
                        )
                    )
                )
                expected = sum(row.delta_bytes for row in rows)
                current = ledger.quota(session, principal.user_id)
                assert current.available_bytes == expected and current.reserved_bytes == 0
                assert len(rows) == current.revision == 25
            report["quota_database_consistent"] = True
            return {"available_bytes": expected, "revision": len(rows)}

    @app.post("/api/v1/__i013__/issue", include_in_schema=False)
    def test_issue() -> dict[str, str]:
        with Session(h.engine) as session:
            attempt = session.scalar(select(LoginAttempt).order_by(LoginAttempt.created_at.desc()))
            assert attempt is not None and attempt.consumed_at is None
            start = StartResponse(
                request_id=attempt.id,
                verifier="TEST-ONLY-UNUSED",
                bot_url="https://t.me/LocalTestLoginBot",
                expires_at=attempt.expires_at,
            )
        return {"code": issue(h, start, subject=2**40 + 1300)}

    @app.post("/api/v1/__i013__/fact", include_in_schema=False)
    def test_fact(value: dict[str, str]) -> dict[str, bool]:
        name = value.get("name")
        assert name in FACTS
        report["facts"][name] = True
        return {"accepted": True}

    @app.post("/api/v1/__i013__/background", include_in_schema=False)
    def test_background() -> dict[str, bool]:
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        time.sleep(2)
        adb("shell", "am", "start", "-n", PACKAGE + "/org.example.vpnpoc.ClientActivity")
        return {"returned": True}

    @app.post("/api/v1/__i013__/revoke", include_in_schema=False)
    def test_revoke(request: Request) -> dict[str, bool]:
        with Session(h.engine) as session:
            active = session.scalar(select(Tunnel).where(Tunnel.state == "active"))
            assert active is not None
            tunnel_id = active.id
        response = h.client.delete(
            "/api/v1/tunnels/" + str(tunnel_id),
            headers={"Authorization": request.headers.get("Authorization", "")},
        )
        assert response.status_code == 200
        return {"requested": True}

    @app.post("/api/v1/__i013__/settled", include_in_schema=False)
    def test_settled() -> dict[str, bool]:
        for _ in range(40):
            with Session(h.engine) as session:
                tunnels = list(session.scalars(select(Tunnel)))
                reservations = list(session.scalars(select(TunnelReservation)))
                ready = bool(tunnels) and all(t.state == "revoked" for t in tunnels)
                ready = ready and all(r.settled_at is not None for r in reservations)
                if ready:
                    if LIVE:
                        assert reservations and all(
                            r.charged_bytes is not None for r in reservations
                        )
                    report["remote_revoked_and_settled"] = True
                    return {"settled": True}
            time.sleep(0.25)
        raise RuntimeError("settlement_not_confirmed")

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
    tunnel: subprocess.Popen[bytes] | None = None
    profile_path = PRIVATE / "i013-node-profile.json"
    remote_touched = False
    stage = "preflight"
    try:
        assert os.name == "nt"
        assert sum(line.endswith("\tdevice") for line in adb("devices").decode().splitlines()) == 1
        if REWARDS:
            assert adb("shell", "getprop", "ro.kernel.qemu").strip() != b"1"
            report["physical_android"] = True
        bootstrap: dict[str, str] = {}
        if LIVE:
            assert (PRIVATE / "staging-key").is_file()
            state = remote("status")
            assert state.get("endpoint_active") is False and state.get("agent_active") is False
            h.settings.tunnel_profile_file = profile_path
            node = NodeRegistry(h.engine).provision(
                ProvisionNodeInput(
                    idempotency_key=uuid4(), name="i013-staging-window", region="staging"
                )
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
        # Install normal app first, then Flutter will update it with the TEST ENTRYPOINT.
        adb(
            "install",
            "-r",
            str(ROOT / "apps/client/build/app/outputs/flutter-apk/app-debug.apk"),
            timeout=90,
        )
        adb("shell", "am", "force-stop", PACKAGE)
        adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "allow")
        adb("reverse", "tcp:18766", "tcp:18766")
        thread.start()
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        if LIVE:
            stage = "staging_start"
            tunnel = transport()
            remote_touched = True
            remote("prepare", json.dumps(bootstrap).encode("ascii"))
            remote("start")
            selected = remote("profile")
            NodeProfile.model_validate(selected)
            profile_path.write_text(json.dumps(selected), encoding="ascii")
            for _ in range(40):
                with Session(h.engine) as session:
                    current = session.get(VPNNode, node.node_id)
                    if (
                        current is not None
                        and current.peer_protocol_version == 2
                        and str(current.peer_runtime_id) == bootstrap["runtime_id"]
                    ):
                        break
                time.sleep(0.25)
            else:
                raise RuntimeError("agent_negotiation_not_observed")
        stage = "flutter_physical_test"
        flutter_sdk = Path(
            os.environ.get(
                "VPN_I013_FLUTTER_SDK",
                os.path.join(os.environ["LOCALAPPDATA"], "ProjectVpn", "toolchains", "flutter"),
            )
        )
        flutter = str(flutter_sdk / "bin/flutter.bat")
        # Explicit local source/toolchain overrides support writable ASCII build
        # snapshots. They affect only this opt-in test, never production config.
        client_directory = Path(os.environ.get("VPN_I013_CLIENT_DIR", ROOT / "apps/client"))
        assert (client_directory / "integration_test/client_api_test.dart").is_file()
        serial = [
            line.split()[0]
            for line in adb("devices").decode().splitlines()
            if line.endswith("\tdevice")
        ][0]
        env = dict(os.environ)
        env["JAVA_HOME"] = r"C:\Program Files\Android\Android Studio\jbr"
        env["PUB_CACHE"] = str(flutter_sdk.parent / "pub-cache")
        result = subprocess.run(  # noqa: S603 - fixed Flutter entrypoint and ADB-observed device
            [
                flutter,
                "test",
                "integration_test/client_api_test.dart",
                "-d",
                serial,
                "--no-pub",
                "--dart-define=VPN_I013_LIVE=" + str(LIVE).lower(),
                "--dart-define=VPN_I014_LOCAL=" + str(QUOTA).lower(),
                "--dart-define=VPN_I015_LOCAL=" + str(REWARDS).lower(),
            ],
            cwd=client_directory,
            env=env,
            capture_output=True,
            timeout=500,
            check=False,
        )  # noqa: S603
        # Never echo raw Flutter/Android output (it could contain a widget's login code).
        success = result.returncode == 0
        if not success:
            output = result.stdout + result.stderr
            report["phone_failure_kind"] = (
                "android_dependency_lock"
                if b"part of the dependency lock state" in output
                else "android_build"
                if b"BUILD FAILED" in output
                else "flutter_test"
            )
        failure_stage = re.search(rb"i013_stage_([a-z_]{1,40})", result.stdout)
        if failure_stage is not None:
            report["phone_failure_stage"] = failure_stage[1].decode("ascii")
        failure_line = re.search(rb"i013_line_([0-9]{1,5})", result.stdout)
        if failure_line is not None:
            report["phone_failure_test_line"] = int(failure_line[1])
        # pytest assertion introspection must never render CompletedProcess.stdout.
        del result
        report["flutter_test_exit_success"] = success
        expected = (
            FACTS - {"admission_closed_no_false_connected"}
            if LIVE
            else {"flutter_auth_ui", "admission_closed_no_false_connected"}
        )
        if QUOTA:
            expected = expected | {"quota_ui_exact_and_paginated"}
        if REWARDS:
            expected = expected | {"reward_history_and_replay"}
        assert success and all(report["facts"].get(key) is True for key in expected)
        if REWARDS:
            assert report.get("reward_database_consistent") is True
        report["passed"] = True
        stage = "cleanup"
    finally:
        if remote_touched:
            try:
                cleanup = remote("expire")
                report["staging_cleanup_complete"] = cleanup.get("cleanup_complete") is True
                report["control_grant_removed"] = cleanup.get("control_grant_removed") is True
                # Controller's pinned receipt parser already allowlists seven baseline flags.
                report["cleanup_receipt"] = cleanup.get("cleanup_receipt", {})
                report["passed"] &= bool(
                    report["staging_cleanup_complete"] and report["control_grant_removed"]
                )
            except (RuntimeError, ValueError, subprocess.SubprocessError):
                report["passed"] = False
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
        phone_clean = True
        try:
            # Flutter test may already uninstall its APK. Absence is a valid clean state.
            if ("package:" + PACKAGE).encode() in adb("shell", "pm", "list", "packages", PACKAGE):
                adb("shell", "am", "force-stop", PACKAGE)
                adb("shell", "appops", "set", PACKAGE, "ACTIVATE_VPN", "default")
        except (RuntimeError, subprocess.SubprocessError):
            # Flutter may uninstall between pm-list and appops; check final state.
            try:
                phone_clean = ("package:" + PACKAGE).encode() not in adb(
                    "shell", "pm", "list", "packages", PACKAGE
                )
            except (RuntimeError, subprocess.SubprocessError):
                phone_clean = False
        try:
            if b"tcp:18766" in adb("reverse", "--list"):
                adb("reverse", "--remove", "tcp:18766")
            phone_clean = phone_clean and b"tcp:18766" not in adb("reverse", "--list")
        except (RuntimeError, subprocess.SubprocessError):
            phone_clean = False
        report["phone_stopped_and_reverse_removed"] = phone_clean
        report["passed"] &= phone_clean
        profile_path.unlink(missing_ok=True)
        report["passed"] &= not thread.is_alive()
        if not report["passed"]:
            report["failed_at"] = stage
        destination = (
            ROOT / "docs/evidence" / ("i-013-" + ("staging" if LIVE else "local-android") + ".json")
        )
        if QUOTA:
            destination = ROOT / "docs/evidence/i-014-local-android.json"
        if REWARDS:
            destination = ROOT / "docs/evidence/i-015-local-android.json"
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert report["passed"], (
        "I-013 physical acceptance failed; inspect sanitized receipt, not raw credentials"
    )

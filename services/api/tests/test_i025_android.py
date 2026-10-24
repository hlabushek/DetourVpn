"""Physical Flutter/Keystore + real disposable API/PostgreSQL. No VPN or WAN changes."""

import json
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from test_authentication import Harness, harness, issue

from vpn_api.auth.contracts import StartResponse
from vpn_api.db.models import LoginAttempt, PeerConnection, SessionCredential, SessionFamily, Tunnel
from vpn_api.main import create_app

__all__ = ["harness"]
ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "org.example.vpnclient"
FACTS = {
    "real_flutter_api_keystore_refresh_rotation",
    "real_flutter_logout_erases_and_server_revokes",
    "lost_committed_refresh_response_no_durable_replay",
}
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("VPN_I025_LOCAL_ANDROID") != "1",
        reason="I-025 physical session not requested",
    ),
]


def test_i025_physical_session(harness: Harness) -> None:
    adb_path = Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"
    serial = os.environ["VPN_I025_SERIAL"]
    client = Path(os.environ["VPN_I025_CLIENT_DIR"])
    flutter_sdk = Path(os.environ["LOCALAPPDATA"]) / "ProjectVpn/toolchains/flutter"
    app_apk = ROOT / "apps/client/build/app/outputs/flutter-apk/app-debug.apk"

    def adb(*parts: str, timeout: int = 90) -> str:
        result = subprocess.run(  # noqa: S603 - fixed local tool and explicit known serial.
            [str(adb_path), "-s", serial, *parts],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("i025_session_adb_failed")
        return result.stdout.decode("utf-8", "replace")

    native_receipt = json.loads((ROOT / "docs/evidence/i-025-native-android.json").read_text())
    assert native_receipt["passed"] is True
    assert datetime.fromisoformat(native_receipt["measured_at"]) > datetime.now(UTC) - timedelta(
        minutes=30
    )
    # Do not touch an installed real session. Only the just-created clean native test target.
    assert "tcp:18766" not in adb("reverse", "--list")
    private_dirs = adb("shell", "run-as", PACKAGE, "ls", "-a").splitlines()
    if "no_backup" in private_dirs:
        assert (
            "session.vault" not in adb("shell", "run-as", PACKAGE, "ls", "no_backup").splitlines()
        )
    h = harness
    h.settings.tunnel_profile_file = None
    h.settings.enable_window_tunnels = False
    app = create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender)
    report: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(),
        "real_api": True,
        "real_postgresql": True,
        "real_android_keystore": True,
        "telegram_delivery": "TEST FAKE ONLY",
        "vpn_dataplane_acceptance": False,
        "remote_changes": False,
        "facts": {},
        "passed": False,
    }
    drop_next = False
    refreshes = 0

    @app.middleware("http")
    async def lost_reply(request: Request, call_next: RequestResponseEndpoint) -> Response:
        nonlocal drop_next, refreshes
        response = await call_next(request)
        if request.url.path == "/api/v1/auth/refresh":
            refreshes += 1
            if drop_next and response.status_code == 200:
                drop_next = False
                # Real transaction committed; simulate loss of the resulting new credentials.
                return Response(status_code=503)
        return response

    @app.post("/api/v1/__i025__/issue", include_in_schema=False)
    def issue_test_code() -> dict[str, str]:
        with Session(h.engine) as session:
            attempt = session.scalar(select(LoginAttempt).order_by(LoginAttempt.created_at.desc()))
            assert attempt is not None
            start = StartResponse(
                request_id=attempt.id,
                verifier="TEST ONLY UNUSED",
                bot_url="https://t.me/LocalTestLoginBot",
                expires_at=attempt.expires_at,
            )
        return {"code": issue(h, start, subject=2**40 + 2500)}

    @app.post("/api/v1/__i025__/drop-next-refresh", include_in_schema=False)
    def arm_drop() -> dict[str, bool]:
        nonlocal drop_next
        drop_next = True
        return {"armed": True}

    @app.post("/api/v1/__i025__/fact", include_in_schema=False)
    def fact(value: dict[str, str]) -> dict[str, bool]:
        name = value.get("name")
        assert name in FACTS
        report["facts"][name] = True
        return {"accepted": True}

    @app.post("/api/v1/__i025__/verify", include_in_schema=False)
    def verify() -> dict[str, bool]:
        with Session(h.engine) as session:
            families = list(session.scalars(select(SessionFamily)))
            credentials = list(session.scalars(select(SessionCredential)))
            assert len(families) == 2 and families[0].user_id == families[1].user_id
            assert sum(f.revoked_at is not None for f in families) == 1
            assert sum(c.rotated_at is not None for c in credentials) == 2
            assert refreshes == 2 and drop_next is False
            assert session.scalar(select(Tunnel.id)) is None
            assert session.scalar(select(PeerConnection.id)) is None
            report["database_session_rotation_and_logout_consistent"] = True
        return {"verified": True}

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
    stage = "start_api"
    try:
        thread.start()
        for _ in range(60):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        adb("reverse", "tcp:18766", "tcp:18766")
        stage = "flutter_session"
        env = dict(os.environ)
        env["JAVA_HOME"] = r"C:\Program Files\Android\Android Studio\jbr"
        env["PUB_CACHE"] = str(flutter_sdk.parent / "pub-cache")
        result = subprocess.run(  # noqa: S603 - fixed Flutter binary and reviewed test target.
            [
                str(flutter_sdk / "bin/flutter.bat"),
                "test",
                "integration_test/i025_session_test.dart",
                "-d",
                serial,
                "--no-pub",
            ],
            cwd=client,
            env=env,
            capture_output=True,
            timeout=500,
            check=False,
        )
        success = result.returncode == 0
        failure = re.search(rb"i025_session_stage_([a-z_]{1,40})", result.stdout + result.stderr)
        if failure:
            report["phone_failure_stage"] = failure[1].decode("ascii")
        # Raw Dart/Android output must not be echoed; it can contain live test credentials.
        del result
        report["flutter_test_exit_success"] = success
        report["passed"] = success and all(report["facts"].get(name) is True for name in FACTS)
        report["passed"] &= report.get("database_session_rotation_and_logout_consistent") is True
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=15)
        clean = not thread.is_alive()
        try:
            adb("shell", "am", "force-stop", PACKAGE)
            adb("reverse", "--remove", "tcp:18766")
            adb("install", "-r", str(app_apk))
            clean &= "tcp:18766" not in adb("reverse", "--list")
        except (RuntimeError, subprocess.SubprocessError):
            clean = False
        report["api_stopped_reverse_removed_ordinary_app_restored"] = clean
        report["passed"] &= clean
        if not report["passed"]:
            report["failed_at"] = stage
        (ROOT / "docs/evidence/i-025-session-android.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    assert report["passed"], "I-025 local session failed; inspect sanitized receipt only"

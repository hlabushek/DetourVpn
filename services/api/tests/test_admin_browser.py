"""Opt-in real browser QA with disposable PostgreSQL and TEST ONLY admin credentials."""

import json
import os
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from fastapi import FastAPI
from test_admin import Harness, admin, credentials

__all__ = ["admin"]
ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    os.getenv("VPN_ADMIN_BROWSER_QA") != "1", reason="Opt-in admin browser QA"
)


def test_real_admin_browser(admin: Harness) -> None:
    h = admin
    app = h.client.app
    assert isinstance(app, FastAPI)
    work = ROOT / ".cache"
    work.mkdir(exist_ok=True)
    marker = work / "admin-qa.stop"
    credentials_file = work / "admin-qa-test-only.json"
    marker.unlink(missing_ok=True)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=18768, access_log=False, log_level="critical")
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not marker.exists():
            # Refresh the synthetic OTP for the operator's browser tool. Never real users.
            credentials_file.write_text(
                json.dumps({"test_only": True, "user_id": str(h.user_id), **credentials(h)}),
                encoding="utf-8",
            )
            time.sleep(1)
        assert marker.exists(), "Browser QA completion was not confirmed"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        credentials_file.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
    assert not thread.is_alive()

"""Opt-in manual browser QA. Real DB/API, TEST ONLY Telegram SDK and reward rules."""

import json
import os
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update
from test_authentication import Harness, harness, headers, login
from test_mini_app import payload
from test_rewards import rule

from vpn_api.db.connection import transaction
from vpn_api.db.models import RewardRule

__all__ = ["harness"]
ROOT = Path(__file__).resolve().parents[3]
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(os.getenv("VPN_MINI_APP_BROWSER_QA") != "1", reason="Opt-in browser QA"),
]


def test_browser_cabinet(harness: Harness) -> None:
    h = harness
    h.auth.settings.enable_mini_app = True
    h.auth.settings.initial_quota_bytes = 12400000000
    daily = rule(h)
    rule(h, "clicker", cap=10)
    with transaction(h.engine) as session:
        session.execute(
            update(RewardRule)
            .where(RewardRule.id == daily)
            .values(reward_bytes=300000000, daily_cap_bytes=300000000)
        )
    referral_test_code = ""
    if os.getenv("VPN_I018_BROWSER_QA") == "1":
        from test_quota_api import owner
        from test_referrals import bind, campaign, code, evidence

        inviter_auth = headers(login(h, 111))
        referral_test_code = code(h, inviter_auth)
        cabinet_auth = headers(login(h))
        own_code = code(h, cabinet_auth)
        invitee_auth = headers(login(h, 456))
        assert bind(h, invitee_auth, own_code, campaign(h)).status_code == 200
        evidence(h, owner(h, invitee_auth))
    raw = payload(h, 2**40) if os.getenv("VPN_I018_BROWSER_QA") == "1" else payload(h)
    dist = ROOT / "apps/telegram-mini-app/dist"
    assert dist.is_dir(), "Build the Mini App first"
    app = h.client.app
    assert isinstance(app, FastAPI)

    @app.get("/mini-app/")
    def index() -> HTMLResponse:
        content = (dist / "index.html").read_text(encoding="utf-8")
        content = content.replace(
            "https://telegram.org/js/telegram-web-app.js", "/mini-app/test-only-sdk.js"
        )
        content = content.replace(
            '<div id="root">',
            '<p style="text-align:center;color:#ffb5bc">TEST ONLY · '
            "синтетический Telegram, локальная БД " + referral_test_code + '</p><div id="root">',
        )
        return HTMLResponse(content)

    @app.get("/mini-app/test-only-sdk.js")
    def sdk() -> Response:
        # initData is generated from an ephemeral TEST credential, never real user data.
        return Response(
            "window.Telegram={WebApp:{initData:"
            + json.dumps(raw)
            + ",colorScheme:'dark',ready(){},expand(){},onEvent(){},offEvent(){}}};",
            media_type="application/javascript",
        )

    app.mount("/mini-app", StaticFiles(directory=dist, html=True))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=18767, access_log=False, log_level="critical")
    )
    thread = Thread(target=server.run, daemon=True)
    stop = ROOT / ".cache/mini-app-qa.stop"
    stop.unlink(missing_ok=True)
    thread.start()
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not stop.exists():
            time.sleep(0.25)
        assert server.started
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        stop.unlink(missing_ok=True)
        assert not thread.is_alive()

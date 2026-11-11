"""ADR-115 panel server delivery. Real PostgreSQL; TEST SYNTHETIC links, no panel is contacted."""

import base64
import secrets
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import SecretStr
from test_admin import synthetic_panel_link
from test_authentication import Harness, harness, headers, login
from test_window_service import enable_http, inventory

from vpn_api.auth.contracts import TokenResponse
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import AdminIdentity, PanelServerSource
from vpn_api.devices import DeviceInput, register_device

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def device_tokens(h: Harness, platform: str, subject: int) -> TokenResponse:
    tokens = login(h, subject)
    register_device(
        h.auth,
        tokens.access_token,
        DeviceInput(
            platform=platform,
            key_type="x25519",
            public_key=base64.b64encode(
                X25519PrivateKey.generate().public_key().public_bytes_raw()
            ).decode(),
        ),
    )
    return tokens


def import_panel(h: Harness, key: bytes, link: str) -> UUID:
    with transaction(h.engine) as session:
        identity = AdminIdentity(
            username="test-panel-admin-" + secrets.token_hex(4),
            role="admin",
            password_hash="TEST-ONLY-not-a-hash",
            totp_ciphertext="TEST-ONLY",
            created_at=database_now(session),
        )
        session.add(identity)
        session.flush()
        row = PanelServerSource(
            label="Тестовая панель",
            panel_kind="remnawave",
            protocol="vless_reality",
            link_ciphertext=Fernet(key).encrypt(link.partition("#")[0].encode()).decode(),
            created_by=identity.id,
            created_at=database_now(session),
        )
        session.add(row)
        session.flush()
        return row.id


def enable(h: Harness, tmp_path: Path, deliver: bool) -> bytes:
    _, selected = inventory(h)
    enable_http(h, selected, tmp_path)
    key = Fernet.generate_key()
    h.settings.admin_encryption_key = SecretStr(key.decode())
    h.settings.deliver_panel_servers = deliver
    return key


def test_panel_rows_are_opt_in_and_off_by_default(harness: Harness, tmp_path: Path) -> None:
    h = harness
    key = enable(h, tmp_path, deliver=False)
    link, uuid, pbk = synthetic_panel_link()
    server_id = import_panel(h, key, link)
    tokens = device_tokens(h, "windows", 3001)
    listed = h.client.get(
        "/api/v1/window-tunnels/nodes", params={"include": "panel"}, headers=headers(tokens)
    )
    assert listed.status_code == 200
    assert all(row["source"] == "vps" for row in listed.json()["nodes"])
    profile = h.client.get(
        f"/api/v1/window-tunnels/panel-servers/{server_id}/profile", headers=headers(tokens)
    )
    assert profile.status_code == 503
    assert profile.json()["error"]["code"] == "panel_servers_unavailable"

    h.settings.deliver_panel_servers = True
    # Without `include=panel` the list stays VPS-only, so older clients never see panel rows.
    plain = h.client.get("/api/v1/window-tunnels/nodes", headers=headers(tokens)).json()["nodes"]
    assert plain and all(r["source"] == "vps" and r["protocol"] == "awg" for r in plain)
    listed = h.client.get(
        "/api/v1/window-tunnels/nodes", params={"include": "panel"}, headers=headers(tokens)
    )
    rows = listed.json()["nodes"]
    panel = [row for row in rows if row["source"] == "panel"]
    assert len(panel) == 1 and len(rows) == len(plain) + 1
    assert panel[0]["id"] == str(server_id) and panel[0]["protocol"] == "vless_reality"
    assert panel[0]["name"] == "Тестовая панель" and panel[0]["available"] is True
    assert panel[0]["probe_host"] is None
    for secret in (uuid, pbk, "panel.example.test", "www.example.test", "vless://"):
        assert secret not in listed.text
    bad = h.client.get(
        "/api/v1/window-tunnels/nodes", params={"include": "all"}, headers=headers(tokens)
    )
    assert bad.status_code == 422


def test_profile_only_for_windows_devices(harness: Harness, tmp_path: Path) -> None:
    h = harness
    key = enable(h, tmp_path, deliver=True)
    link, uuid, pbk = synthetic_panel_link()
    server_id = import_panel(h, key, link)
    path = f"/api/v1/window-tunnels/panel-servers/{server_id}/profile"

    assert h.client.get(path).status_code == 401
    android = device_tokens(h, "android", 3002)
    refused = h.client.get(path, headers=headers(android))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "panel_platform_unsupported"
    assert uuid not in refused.text

    windows = device_tokens(h, "windows", 3003)
    response = h.client.get(path, headers=headers(windows))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "id": str(server_id),
        "protocol": "vless_reality",
        "server": "panel.example.test",
        "port": 443,
        "uuid": uuid,
        "flow": "xtls-rprx-vision",
        "sni": "www.example.test",
        "public_key": pbk,
        "short_id": "0a1b",
        "fingerprint": "chrome",
    }
    missing = h.client.get(
        f"/api/v1/window-tunnels/panel-servers/{uuid4()}/profile", headers=headers(windows)
    )
    assert missing.status_code == 404


def test_rotated_admin_key_makes_the_server_unreadable(harness: Harness, tmp_path: Path) -> None:
    h = harness
    key = enable(h, tmp_path, deliver=True)
    server_id = import_panel(h, key, synthetic_panel_link()[0])
    h.settings.admin_encryption_key = SecretStr(Fernet.generate_key().decode())
    windows = device_tokens(h, "windows", 3004)
    response = h.client.get(
        f"/api/v1/window-tunnels/panel-servers/{server_id}/profile", headers=headers(windows)
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "panel_server_unreadable"

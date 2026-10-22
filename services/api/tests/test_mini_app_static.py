from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vpn_api.config import Settings
from vpn_api.main import create_app


def test_empty_static_directory_is_not_checkout() -> None:
    assert Settings(_env_file=None, mini_app_static_dir="").mini_app_static_dir is None


def test_mini_app_cannot_enable_without_auth() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, enable_mini_app=True)


def test_assets_disabled_by_default(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("SHOULD_NOT_SERVE")
    with TestClient(create_app(Settings(_env_file=None, mini_app_static_dir=tmp_path))) as client:
        assert client.get("/mini-app/").status_code == 404


def test_static_security_headers(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html lang='ru'>Cabinet</html>")
    settings = Settings(_env_file=None)
    # TEST ONLY: bypass service startup to isolate static serving from PostgreSQL.
    settings.enable_mini_app = True
    settings.mini_app_static_dir = tmp_path
    with TestClient(create_app(settings)) as client:
        response = client.get("/mini-app/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        csp = response.headers["content-security-policy"]
        assert "connect-src 'self'" in csp and "https://telegram.org" in csp
        assert "form-action 'none'" in csp

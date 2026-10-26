from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "beta_activate_auth.py"
SPEC = importlib.util.spec_from_file_location("beta_activate_auth", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


@pytest.fixture
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "releases"
    path = root / ("a" * 32)
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(activation, "RELEASE_ROOT", root)
    return path


@pytest.fixture
def closed_environment() -> str:
    return "\n".join(
        (
            "VPN_ENVIRONMENT=closed",
            "VPN_PUBLIC_ORIGIN=https://127.0.0.1",
            "VPN_ENABLE_ADMIN=true",
            "VPN_CLOSED_ADMIN_ONLY=true",
            "VPN_ENABLE_AUTH=false",
            "VPN_ENABLE_MINI_APP=false",
            "VPN_BETA_INVITES_ONLY=false",
            "VPN_TELEGRAM_BOT_TOKEN=123456:abcdefghijklmnopqrstuvwxyz123456789",
            "VPN_TELEGRAM_BOT_ID=123456",
            "VPN_TELEGRAM_BOT_USERNAME=uysdgyfudf_bot",
            "",
        )
    )


def test_render_activation_enables_auth_only_with_invite_gate(
    closed_environment: str, release: Path
) -> None:
    rendered = activation.render_activation(
        closed_environment, str(release), "A" * 43, "B" * 43
    )
    _, values = activation.parse_environment(rendered)
    assert values["VPN_CLOSED_ADMIN_ONLY"] == "false"
    assert values["VPN_ENABLE_AUTH"] == "true"
    assert values["VPN_ENABLE_MINI_APP"] == "true"
    assert values["VPN_BETA_INVITES_ONLY"] == "true"
    assert values["VPN_MINI_APP_STATIC_DIR"] == str(release)
    assert values["VPN_AUTH_PEPPER"] == "A" * 43
    assert values["VPN_TELEGRAM_WEBHOOK_SECRET"] == "B" * 43


@pytest.mark.parametrize(
    ("change", "error"),
    (
        ("VPN_BETA_INVITES_ONLY=true", "beta_closed_admin_mode_required"),
        ("VPN_TELEGRAM_BOT_USERNAME=wrong_bot", "beta_bot_configuration_required"),
    ),
)
def test_render_activation_fails_closed(
    closed_environment: str, release: Path, change: str, error: str
) -> None:
    key = change.partition("=")[0]
    existing = "\n".join(
        f"{key}={change.partition('=')[2]}" if line.startswith(key + "=") else line
        for line in closed_environment.splitlines()
    )
    with pytest.raises(ValueError, match=error):
        activation.render_activation(existing, str(release), "A" * 43, "B" * 43)


def test_render_activation_rejects_duplicate_keys(
    closed_environment: str, release: Path
) -> None:
    with pytest.raises(ValueError, match="duplicate_beta_environment_key"):
        activation.render_activation(
            closed_environment + "VPN_ENABLE_AUTH=false\n",
            str(release),
            "A" * 43,
            "B" * 43,
        )


def test_render_activation_rejects_invalid_release_and_secrets(
    closed_environment: str, release: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="beta_mini_app_release_invalid"):
        activation.render_activation(
            closed_environment, str(tmp_path), "A" * 43, "B" * 43
        )
    with pytest.raises(ValueError, match="beta_auth_secret_invalid"):
        activation.render_activation(
            closed_environment, str(release), "short", "B" * 43
        )

from __future__ import annotations

import pytest
from scripts.activate_beta_node import (
    API_ACTIVATE_ROW,
    API_ENABLE,
    API_PREFLIGHT,
    API_RESTART,
    API_ROLLBACK,
    API_ROLLBACK_PREFLIGHT,
    API_WAIT_RUNTIME,
    NODE_ACTIVE_CHECK,
    NODE_PREFLIGHT,
    NODE_START,
    NODE_STOP,
    ActivationError,
    render_api_environment,
)


@pytest.mark.parametrize(
    "remote_program",
    [
        API_ACTIVATE_ROW,
        API_ENABLE,
        API_PREFLIGHT,
        API_RESTART,
        API_ROLLBACK,
        API_ROLLBACK_PREFLIGHT,
        API_WAIT_RUNTIME,
        NODE_ACTIVE_CHECK,
        NODE_PREFLIGHT,
        NODE_START,
        NODE_STOP,
    ],
)
def test_embedded_activation_programs_compile(remote_program: str) -> None:
    compile(remote_program, "<beta-activation-program>", "exec")


def test_render_activation_preserves_existing_settings_and_line_endings() -> None:
    original = (
        b"VPN_ENVIRONMENT=closed\r\n"
        b"VPN_ENABLE_AUTH=true\r\n"
        b"VPN_ENABLE_WINDOW_TUNNELS=false\r\n"
        b"VPN_WINDOW_MANIFEST_FILE=\r\n"
        b"# retain comment\r\n"
    )

    result = render_api_environment(original)

    assert result == original.replace(
        b"VPN_ENABLE_WINDOW_TUNNELS=false",
        b"VPN_ENABLE_WINDOW_TUNNELS=true",
    ).replace(
        b"VPN_WINDOW_MANIFEST_FILE=\r\n",
        b"VPN_WINDOW_MANIFEST_FILE=/etc/detourvpn-beta/window-manifest.json\r\n",
    )


def test_render_activation_appends_only_missing_settings() -> None:
    result = render_api_environment(b"VPN_ENVIRONMENT=closed")

    assert result.endswith(
        b"VPN_ENABLE_WINDOW_TUNNELS=true\n"
        b"VPN_WINDOW_MANIFEST_FILE=/etc/detourvpn-beta/window-manifest.json\n"
    )
    assert result.count(b"VPN_ENABLE_WINDOW_TUNNELS=") == 1
    assert result.count(b"VPN_WINDOW_MANIFEST_FILE=") == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"VPN_ENABLE_WINDOW_TUNNELS=true\n",
        b"VPN_WINDOW_MANIFEST_FILE=/unexpected/path\n",
        b"VPN_ENABLE_WINDOW_TUNNELS=false\nVPN_ENABLE_WINDOW_TUNNELS=false\n",
        b"\xff",
    ],
)
def test_render_activation_refuses_unexpected_or_ambiguous_state(raw: bytes) -> None:
    with pytest.raises(ActivationError):
        render_api_environment(raw)

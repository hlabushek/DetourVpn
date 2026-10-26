"""Offline beta config tests using in-memory synthetic TEST ONLY keys."""

from __future__ import annotations

import base64
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from stat import S_IMODE
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "node-agent"))

from beta_slot_config import ConfigError, render_configs  # noqa: E402
from scripts.prepare_beta_node_config import OutputError, write_configs  # noqa: E402

from vpn_api.tunnels.window_profiles import WindowManifest  # noqa: E402


def keypair(seed: bytes) -> tuple[str, str]:
    private = base64.b64encode(seed).decode("ascii")
    public_raw = (
        X25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    return private, base64.b64encode(public_raw).decode("ascii")


def source(slots: list[dict[str, object]] | None = None) -> bytes:
    private, _public = keypair(bytes(range(1, 33)))
    return json.dumps(
        {
            "version": 1,
            "node_id": str(uuid4()),
            "api_origin": "https://beta-api.example.invalid",
            "slots": slots
            or [
                {
                    "slot_id": 1,
                    "endpoint": "8.8.8.8:51901",
                    "server_private_key": private,
                    "header_protection_key": base64.b64encode(bytes(range(33, 65))).decode(),
                }
            ],
        }
    ).encode()


def valid_global_source() -> bytes:
    return source()


def test_rendered_node_and_api_configs_share_stable_public_and_header_keys() -> None:
    raw = valid_global_source()
    rendered = render_configs(raw)
    node = json.loads(rendered["node_slots"])
    api = json.loads(rendered["api_manifest"])
    validated = WindowManifest.model_validate_json(rendered["api_manifest"])
    assert validated.version == 3
    node_slot = node["slots"][0]
    api_slot = api["nodes"][0]["slots"][0]
    assert node_slot["server_public_key"] == api_slot["server_public_key"]
    assert node_slot["header_protection_key"] == api_slot["header_protection_key"]
    assert node_slot["server_private_key"] not in rendered["api_manifest"].decode()
    assert b"server_private_key" not in rendered["api_manifest"]
    assert "credential" not in json.loads(rendered["agent_config"])
    assert json.loads(rendered["agent_config"])["slot_count"] == 1
    awg = rendered["awg_configs"]["slot-1.conf"].decode("ascii")
    assert f"PrivateKey = {node_slot['server_private_key']}\n" in awg
    assert "ListenPort = 51901\n" in awg
    assert "HeaderProtectionKey = " + node_slot["header_protection_key"] + "\n" in awg


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value.update(api_origin="http://api.example.invalid"),
            "beta_slot_config_invalid",
        ),
        (lambda value: value.update(version=2), "beta_slot_config_invalid"),
        (
            lambda value: value["slots"][0].update(endpoint="8.8.8.8:51902"),
            "beta_slot_profile_invalid",
        ),
        (
            lambda value: value["slots"][0].update(endpoint="192.168.1.5:51901"),
            "beta_slot_profile_invalid",
        ),
        (
            lambda value: value["slots"][0].update(header_protection_key="bad"),
            "beta_slot_profile_invalid",
        ),
        (
            lambda value: value["slots"][0].update(server_private_key="bad"),
            "server_private_key_invalid",
        ),
    ],
)
def test_invalid_config_fails_closed_without_echoing_input(
    mutate: Callable[[dict[str, object]], None], expected: str
) -> None:
    value = json.loads(valid_global_source())
    mutate(value)  # type: ignore[arg-type]
    raw = json.dumps(value).encode()
    with pytest.raises(ConfigError, match=expected) as error:
        render_configs(raw)
    assert value["slots"][0]["server_private_key"] not in str(error.value)


def test_noncontiguous_slots_and_oversized_input_are_rejected() -> None:
    value = json.loads(valid_global_source())
    private, _public = keypair(bytes(range(65, 97)))
    value["slots"].append(
        {
            "slot_id": 3,
            "endpoint": "8.8.4.4:51903",
            "server_private_key": private,
            "header_protection_key": base64.b64encode(bytes(range(97, 129))).decode(),
        }
    )
    with pytest.raises(ConfigError, match="beta_slot_config_invalid"):
        render_configs(json.dumps(value).encode())
    with pytest.raises(ConfigError, match="beta_slot_config_size_invalid"):
        render_configs(b" " * 65537)


def test_duplicate_server_key_is_rejected_without_echoing_keys() -> None:
    value = json.loads(valid_global_source())
    second = dict(value["slots"][0])
    second["slot_id"] = 2
    second["endpoint"] = "8.8.4.4:51902"
    second["header_protection_key"] = base64.b64encode(bytes(range(65, 97))).decode()
    value["slots"].append(second)
    with pytest.raises(ConfigError, match="beta_window_manifest_invalid") as error:
        render_configs(json.dumps(value).encode())
    assert value["slots"][0]["server_private_key"] not in str(error.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX private-file permissions required")
def test_writer_creates_separate_private_outputs(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    private_parent = tmp_path / "private"
    repository.mkdir()
    private_parent.mkdir(mode=0o700)
    private_parent.chmod(0o700)
    source_path = private_parent / "source.json"
    source_path.write_bytes(valid_global_source())
    source_path.chmod(0o600)
    output = private_parent / "rendered"

    paths = write_configs(source_path, output, repository)
    node_config = json.loads((output / "node/slots.json").read_bytes())
    api_manifest = json.loads((output / "api/window-manifest.json").read_bytes())
    private_key = node_config["slots"][0]["server_private_key"]
    assert paths == [
        "node/agent.json",
        "node/slots.json",
        "node/awg/slot-1.conf",
        "api/window-manifest.json",
    ]
    assert private_key not in (output / "api/window-manifest.json").read_text()
    assert S_IMODE(output.stat().st_mode) == 0o700
    assert S_IMODE((output / "node").stat().st_mode) == 0o700
    assert S_IMODE((output / "api").stat().st_mode) == 0o700
    assert S_IMODE((output / "node/agent.json").stat().st_mode) == 0o600
    assert S_IMODE((output / "node/slots.json").stat().st_mode) == 0o600
    assert S_IMODE((output / "node/awg").stat().st_mode) == 0o700
    assert S_IMODE((output / "node/awg/slot-1.conf").stat().st_mode) == 0o600
    assert S_IMODE((output / "api/window-manifest.json").stat().st_mode) == 0o600
    assert api_manifest["version"] == 3


@pytest.mark.skipif(os.name == "posix", reason="Windows permission check")
def test_writer_fails_closed_when_ntfs_acl_cannot_be_verified(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with pytest.raises(OutputError, match="private_posix_output_required"):
        write_configs(tmp_path / "missing-source.json", output, ROOT)
    assert not output.exists()

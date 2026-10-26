from __future__ import annotations

import base64
import json
from uuid import UUID

import pytest
from scripts.bind_beta_node_config import (
    CLEANUP_CREDENTIAL,
    READ_CREDENTIAL,
    READ_SLOTS,
    STAGE_API_MANIFEST,
    STAGE_NODE,
    VERIFY_API,
    VERIFY_NODE,
    BindingError,
    build_manifest,
    build_node_payload,
)

NODE_ID = UUID("1d230f49-ed36-45f2-91ea-86fe212346f8")
SERVER_PRIVATE = base64.b64encode(bytes(range(1, 33))).decode("ascii")
SERVER_PUBLIC = base64.b64encode(bytes(range(33, 65))).decode("ascii")
HEADER_KEY = base64.b64encode(bytes(range(65, 97))).decode("ascii")
CREDENTIAL = "A" * 43


@pytest.mark.parametrize(
    "remote_program",
    [
        CLEANUP_CREDENTIAL,
        READ_CREDENTIAL,
        READ_SLOTS,
        STAGE_API_MANIFEST,
        STAGE_NODE,
        VERIFY_API,
        VERIFY_NODE,
    ],
)
def test_embedded_remote_programs_are_valid_python(remote_program: str) -> None:
    compile(remote_program, "<embedded-remote-program>", "exec")


def slots_source(endpoint: str = "95.81.73.183:51901") -> bytes:
    return json.dumps(
        {
            "version": 1,
            "slots": [
                {
                    "slot_id": 1,
                    "endpoint": endpoint,
                    "server_private_key": SERVER_PRIVATE,
                    "server_public_key": SERVER_PUBLIC,
                    "header_protection_key": HEADER_KEY,
                }
            ],
        }
    ).encode()


def test_manifest_contains_only_client_verification_material() -> None:
    manifest = build_manifest(NODE_ID, slots_source())
    parsed = json.loads(manifest)

    assert parsed["version"] == 3
    assert parsed["nodes"][0]["node_id"] == str(NODE_ID)
    assert parsed["nodes"][0]["slots"][0]["endpoint"] == "95.81.73.183:51901"
    assert SERVER_PUBLIC.encode() in manifest
    assert HEADER_KEY.encode() in manifest
    assert SERVER_PRIVATE.encode() not in manifest


def test_manifest_rejects_other_node_endpoint() -> None:
    with pytest.raises(BindingError, match="^beta_node_manifest_invalid$"):
        build_manifest(NODE_ID, slots_source("1.1.1.1:51901"))


def test_node_payload_contains_credential_but_no_slot_private_keys() -> None:
    record = json.dumps({"version": 1, "node_id": str(NODE_ID), "credential": CREDENTIAL}).encode()

    payload = build_node_payload(NODE_ID, record, 1)
    parsed = json.loads(payload)

    assert parsed["credential"] == CREDENTIAL
    assert parsed["node_id"] == str(NODE_ID)
    assert "server_private_key" not in parsed
    assert "header_protection_key" not in parsed


def test_node_payload_rejects_mismatched_identity_and_extra_fields() -> None:
    record = json.dumps(
        {
            "version": 1,
            "node_id": str(NODE_ID),
            "credential": CREDENTIAL,
            "extra": "forbidden",
        }
    ).encode()

    with pytest.raises(BindingError, match="^beta_node_credential_invalid$"):
        build_node_payload(NODE_ID, record, 1)

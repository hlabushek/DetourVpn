"""In-app update manifest contract (ADR-107). Synthetic values only; no DB needed.

Signing keys here are TEST ONLY throwaway Ed25519 keys created in memory per test. No private
key is written to disk by these tests.
"""

import base64
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from vpn_api import app_update
from vpn_api.app_update import (
    MAX_MANIFEST_BYTES,
    UPDATE_SIGNING_PUBLIC_KEY,
    AppUpdateManifest,
    key_id,
    load_manifest,
    sign_manifest,
)
from vpn_api.config import Settings
from vpn_api.main import create_app

ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 64


def manifest(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "version_code": 30,
        "version_name": "1.2.3",
        "min_supported_version_code": 20,
        "apk_url": "https://updates.example.test/detourvpn-1.2.3.apk",
        "apk_sha256": SHA,
        "apk_size_bytes": 25_000_000,
        "release_notes_ru": "Исправления и улучшения.",
    }
    value.update(overrides)
    return value


def public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def signed_raw(key: Ed25519PrivateKey, value: dict[str, object]) -> dict[str, str]:
    """Signs arbitrary JSON (even an invalid manifest), as a broken release tool might."""
    payload = json.dumps(value, ensure_ascii=False).encode()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "payload": base64.b64encode(payload).decode(),
        "signature": base64.b64encode(key.sign(payload)).decode(),
        "key_id": key_id(public),
    }


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def test_valid_signed_manifest_loads(tmp_path: Path, key: Ed25519PrivateKey) -> None:
    envelope = sign_manifest(AppUpdateManifest.model_validate(manifest()), key)
    loaded = load_manifest(write(tmp_path / "m.json", envelope), public_b64(key))
    assert loaded is not None
    assert loaded.version_code == 30 and loaded.apk_sha256 == SHA


@pytest.mark.parametrize(
    "override",
    [
        {"apk_url": "http://updates.example.test/a.apk"},
        {"apk_sha256": "A" * 64},
        {"apk_size_bytes": 0},
        {"min_supported_version_code": 31},
        {"version_name": "bad name"},
        {"extra": "field"},
    ],
)
def test_signed_but_invalid_manifest_is_unavailable(
    tmp_path: Path, key: Ed25519PrivateKey, override: dict[str, object]
) -> None:
    path = write(tmp_path / "m.json", signed_raw(key, manifest(**override)))
    assert load_manifest(path, public_b64(key)) is None


def test_unsigned_tampered_and_wrong_key_are_unavailable(
    tmp_path: Path, key: Ed25519PrivateKey
) -> None:
    good = sign_manifest(AppUpdateManifest.model_validate(manifest()), key)
    # The old unsigned format is no longer accepted.
    assert load_manifest(write(tmp_path / "plain.json", manifest()), public_b64(key)) is None
    # Tampered payload: same signature, version changed.
    tampered_payload = json.dumps(manifest(version_code=31), separators=(",", ":")).encode()
    tampered = {**good, "payload": base64.b64encode(tampered_payload).decode()}
    assert load_manifest(write(tmp_path / "t.json", tampered), public_b64(key)) is None
    # Flipped signature byte.
    flipped = bytearray(base64.b64decode(good["signature"]))
    flipped[0] ^= 1
    bad_sig = {**good, "signature": base64.b64encode(bytes(flipped)).decode()}
    assert load_manifest(write(tmp_path / "s.json", bad_sig), public_b64(key)) is None
    # Wrong key: verified against another key (with or without its key id).
    other = Ed25519PrivateKey.generate()
    path = write(tmp_path / "good.json", good)
    assert load_manifest(path, public_b64(other)) is None
    relabelled = {**good, "key_id": key_id(base64.b64decode(public_b64(other)))}
    assert load_manifest(write(tmp_path / "k.json", relabelled), public_b64(other)) is None
    # No key configured (the repository default until the owner makes one) serves nothing.
    assert load_manifest(path, "") is None
    assert load_manifest(path, public_b64(key)) is not None


def test_missing_unset_oversized_and_non_json_are_unavailable(
    tmp_path: Path, key: Ed25519PrivateKey
) -> None:
    public = public_b64(key)
    assert load_manifest(None, public) is None
    assert load_manifest(tmp_path / "absent.json", public) is None
    big = tmp_path / "big.json"
    big.write_text("{" + " " * (MAX_MANIFEST_BYTES * 2) + "}", encoding="utf-8")
    assert load_manifest(big, public) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_manifest(broken, public) is None


@pytest.fixture
def client_with(
    tmp_path: Path, key: Ed25519PrivateKey
) -> Iterator[tuple[TestClient, Path, Ed25519PrivateKey]]:
    path = tmp_path / "update.json"
    settings = Settings(
        _env_file=None,
        environment="test",
        app_update_manifest_file=path,
        app_update_public_key=public_b64(key),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client, path, key


def test_endpoints_serve_only_a_verified_manifest(
    client_with: tuple[TestClient, Path, Ed25519PrivateKey],
) -> None:
    client, path, key = client_with
    for route in ("/api/v1/app/update-manifest", "/api/v1/app/signed-update-manifest"):
        missing = client.get(route)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "update_unavailable"
    envelope = sign_manifest(AppUpdateManifest.model_validate(manifest()), key)
    write(path, envelope)
    signed = client.get("/api/v1/app/signed-update-manifest")
    assert signed.status_code == 200 and signed.json() == envelope
    plain = client.get("/api/v1/app/update-manifest")
    assert plain.status_code == 200 and plain.json() == manifest()
    write(path, signed_raw(Ed25519PrivateKey.generate(), manifest()))
    for route in ("/api/v1/app/update-manifest", "/api/v1/app/signed-update-manifest"):
        assert client.get(route).status_code == 404


def test_app_and_api_carry_the_same_public_key() -> None:
    java = (
        ROOT / "apps/client/android/app/src/main/java/org/example/vpnpoc/UpdateSignature.java"
    ).read_text(encoding="utf-8")
    found = re.search(r'static final String PUBLIC_KEY_B64 = "([A-Za-z0-9+/=]*)";', java)
    assert found is not None
    assert found.group(1) == UPDATE_SIGNING_PUBLIC_KEY
    if UPDATE_SIGNING_PUBLIC_KEY:
        assert len(base64.b64decode(UPDATE_SIGNING_PUBLIC_KEY, validate=True)) == 32


def run_prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key: Ed25519PrivateKey,
    signing_key: Path,
    public: str,
) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import prepare_app_update

    # The key is handed over in memory; nothing reads or writes key material on disk.
    monkeypatch.setattr(prepare_app_update, "load_signing_key", lambda _path: key)
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"TEST ONLY not an apk")
    notes = tmp_path / "notes.txt"
    notes.write_text("Исправления", encoding="utf-8")
    return prepare_app_update.main(
        [
            "--apk", str(apk), "--version-code", "30", "--version-name", "1.2.3",
            "--min-supported-version-code", "20",
            "--apk-url", "https://updates.example.test/a.apk",
            "--notes-file", str(notes), "--out", str(tmp_path / "out"),
            "--signing-key", str(signing_key),
        ],
        public_key_b64=public,
    )  # fmt: skip


def test_prepare_script_signs_and_refuses_unsafe_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: Ed25519PrivateKey
) -> None:
    outside = tmp_path / "placeholder-key-path"
    outside.write_text("TEST ONLY placeholder, not a key", encoding="utf-8")
    assert run_prepare(monkeypatch, tmp_path, key, outside, public_b64(key)) == 0
    written = json.loads((tmp_path / "out" / "app-update.json").read_text(encoding="utf-8"))
    loaded = app_update.verify_envelope(written, public_b64(key))
    assert loaded is not None and loaded[1].version_code == 30
    # A key file inside the repository is refused before it is read.
    (tmp_path / "out" / "app-update.json").unlink()
    inside = ROOT / "README.md"
    assert run_prepare(monkeypatch, tmp_path, key, inside, public_b64(key)) == 2
    # A key that does not match the app's public key writes nothing.
    other = public_b64(Ed25519PrivateKey.generate())
    assert run_prepare(monkeypatch, tmp_path, key, outside, other) == 2
    assert run_prepare(monkeypatch, tmp_path, key, outside, "") == 2
    assert not (tmp_path / "out" / "app-update.json").exists()


def test_keygen_refuses_repository_paths_existing_files_and_weak_passphrases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import app_update_keygen

    monkeypatch.setattr(app_update_keygen.getpass, "getpass", lambda _prompt: "short")
    assert app_update_keygen.main(["--private-key-out", str(ROOT / "never.pem")]) == 2
    existing = tmp_path / "existing.pem"
    existing.write_text("TEST ONLY", encoding="utf-8")
    assert app_update_keygen.main(["--private-key-out", str(existing)]) == 2
    assert existing.read_text(encoding="utf-8") == "TEST ONLY"
    assert app_update_keygen.main(["--private-key-out", str(tmp_path / "new.pem")]) == 2
    assert not (tmp_path / "new.pem").exists() and not (ROOT / "never.pem").exists()

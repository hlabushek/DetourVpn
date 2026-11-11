"""In-app update manifest contract (ADR-107). Synthetic values only; no DB needed."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vpn_api.app_update import MAX_MANIFEST_BYTES, load_manifest
from vpn_api.config import Settings
from vpn_api.main import create_app

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


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_valid_manifest_loads(tmp_path: Path) -> None:
    loaded = load_manifest(write(tmp_path / "m.json", manifest()))
    assert loaded is not None and loaded.version_code == 30


@pytest.mark.parametrize(
    "override",
    [
        {"apk_url": "http://updates.example.test/a.apk"},
        {"apk_url": "https://updates.example.test/a b.apk"},
        {"apk_sha256": "A" * 64},
        {"apk_sha256": "a" * 63},
        {"version_name": "1.2.3; rm -rf"},
        {"min_supported_version_code": 31},
        {"version_code": 0},
        {"unexpected": True},
        {"apk_size_bytes": 0},
    ],
)
def test_invalid_manifest_is_unavailable_not_partial(tmp_path: Path, override: dict) -> None:
    assert load_manifest(write(tmp_path / "m.json", manifest(**override))) is None


def test_missing_unset_oversized_and_non_json_are_unavailable(tmp_path: Path) -> None:
    assert load_manifest(None) is None
    assert load_manifest(tmp_path / "absent.json") is None
    big = tmp_path / "big.json"
    big.write_text("{" + " " * MAX_MANIFEST_BYTES + "}", encoding="utf-8")
    assert load_manifest(big) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_manifest(broken) is None


@pytest.fixture
def client_with(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    path = tmp_path / "update.json"
    settings = Settings(_env_file=None, environment="test", app_update_manifest_file=path)
    with TestClient(create_app(settings)) as test_client:
        yield test_client, path


def test_endpoint_serves_the_latest_manifest_only(client_with: tuple[TestClient, Path]) -> None:
    client, path = client_with
    missing = client.get("/api/v1/app/update-manifest")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "update_unavailable"
    write(path, manifest())
    response = client.get("/api/v1/app/update-manifest")
    assert response.status_code == 200
    body = response.json()
    assert body["version_code"] == 30 and body["apk_url"].startswith("https://")
    assert set(body) == set(manifest())
    write(path, manifest(apk_url="http://updates.example.test/a.apk"))
    assert client.get("/api/v1/app/update-manifest").status_code == 404

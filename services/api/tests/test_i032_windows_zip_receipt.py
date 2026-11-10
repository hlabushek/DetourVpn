"""Windows zip receipt (I-032). Archives here are synthetic fixtures, not real binaries."""

from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest
from scripts.verify_windows_zip import VerificationError, main, read_pe, verify_zip

VERSION = "0.1.0-beta.26"
ROOT = "DetourVpn"


def fake_pe(
    *,
    machine: int = 0x8664,
    version: str | None = None,
    admin: bool = False,
    invoker: bool = False,
    signed: bool = False,
    pad: int = 0,
) -> bytes:
    data = bytearray(0x400)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    optional = 0x80 + 24
    struct.pack_into("<H", data, optional, 0x20B)
    if signed:
        struct.pack_into("<II", data, optional + 112 + 4 * 8, 0x300, 0x100)
    tail = b""
    if version:
        tail += "ProductVersion".encode("utf-16le") + b"\0\0\0\0"
        tail += version.encode("utf-16le") + b"\0\0"
    if admin:
        tail += b'<requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>'
    if invoker:
        tail += b'<requestedExecutionLevel level="asInvoker"/>'
    return bytes(data) + tail + b"\0" * pad


class Fixture:
    """A synthetic repository (source-lock + upstream files) and a matching good archive."""

    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "repo"
        amnezia = self.repo / "third_party" / "amnezia-box"
        amnezia.mkdir(parents=True)
        self.wintun = fake_pe()
        self.source = {
            "amnezia-box-test-src.zip": b"fake upstream source archive",
            "peer-stats.patch": b"fake patch one",
            "windows-peer-stats-stdout.patch": b"fake patch two",
        }
        for name, content in self.source.items():
            (amnezia / name).write_bytes(content)
        self.lock = {
            "version": "v0.0.0-test",
            "archive": "amnezia-box-test-src.zip",
            "archive_sha256": hashlib.sha256(self.source["amnezia-box-test-src.zip"]).hexdigest(),
            "windows_patches": ["peer-stats.patch", "windows-peer-stats-stdout.patch"],
            "windows_wintun": {"dll_sha256": hashlib.sha256(self.wintun).hexdigest()},
        }
        lock_bytes = json.dumps(self.lock).encode()
        (amnezia / "source-lock.json").write_bytes(lock_bytes)
        self.source["source-lock.json"] = lock_bytes
        self.tmp_path = tmp_path

    def entries(self) -> dict[str, bytes]:
        files = {
            f"{ROOT}/DetourVpn.exe": fake_pe(version=VERSION + "+27", admin=True),
            f"{ROOT}/detour-engine.exe": fake_pe(pad=5 * 1024 * 1024 + 1),
            f"{ROOT}/wintun.dll": self.wintun,
            f"{ROOT}/flutter_windows.dll": b"fake flutter engine",
            f"{ROOT}/data/app.so": b"fake aot",
            f"{ROOT}/data/icudtl.dat": b"fake icu",
            f"{ROOT}/README.txt": b"readme",
            f"{ROOT}/THIRD_PARTY_NOTICES.md": b"notices",
        }
        for name, content in self.source.items():
            files[f"{ROOT}/SOURCE/{name}"] = content
        return files

    def build(
        self,
        overrides: dict[str, bytes | None] | None = None,
        name: str = f"DetourVpn-windows-x64-{VERSION}.zip",
    ) -> Path:
        files = self.entries()
        for entry, content in (overrides or {}).items():
            if content is None:
                files.pop(entry, None)
            else:
                files[entry] = content
        path = self.tmp_path / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry, content in files.items():
                archive.writestr(entry, content)
        return path


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


def verify(fx: Fixture, archive: Path, expected_version: str | None = None) -> dict[str, object]:
    return verify_zip(archive, fx.repo, expected_version, fx.tmp_path / "receipt.json")


def test_good_archive_produces_a_sanitized_unsigned_receipt(fx: Fixture) -> None:
    archive = fx.build()
    receipt = verify(fx, archive, expected_version=VERSION)
    assert receipt["version_name"] == VERSION
    assert receipt["artifact_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert receipt["signature"] == "unsigned" and receipt["elevation"] == "requireAdministrator"
    assert receipt["distribution"] == "not_accepted"
    assert receipt["runtime_verified"] is False and receipt["tunnel_handshake"] == "not_verified"
    components = receipt["components"]
    assert isinstance(components, dict) and set(components) == {
        "DetourVpn.exe",
        "detour-engine.exe",
        "wintun.dll",
    }
    written = (fx.tmp_path / "receipt.json").read_text("utf-8")
    assert json.loads(written) == receipt
    assert str(fx.tmp_path) not in written  # no local paths, hosts, keys or logs


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({f"{ROOT}/detour-engine.exe": None}, "missing a required file"),
        ({f"{ROOT}/wintun.dll": fake_pe(pad=1)}, "wintun.dll does not match"),
        ({f"{ROOT}/SOURCE/peer-stats.patch": b"tampered patch"}, "differs from the repository"),
        ({f"{ROOT}/SOURCE/peer-stats.patch": None}, "missing the corresponding source"),
        ({f"{ROOT}/DetourVpn.exe": fake_pe(version="9.9.9", admin=True)}, "version does not match"),
        (
            {f"{ROOT}/DetourVpn.exe": fake_pe(version=VERSION, invoker=True)},
            "require administrator",
        ),
        ({f"{ROOT}/DetourVpn.exe": fake_pe(version=VERSION)}, "require administrator"),
        (
            {f"{ROOT}/DetourVpn.exe": fake_pe(version=VERSION, admin=True, signed=True)},
            "unexpected Authenticode",
        ),
        (
            {f"{ROOT}/DetourVpn.exe": fake_pe(machine=0x14C, version=VERSION, admin=True)},
            "not an x64",
        ),
        ({f"{ROOT}/detour-engine.exe": fake_pe()}, "plausible x64 engine"),
        ({f"{ROOT}/DetourVpn.pdb": b"symbols"}, "must not be shipped"),
        ({f"{ROOT}/data/release.key": b"secret"}, "must not be shipped"),
        ({f"{ROOT}/.env": b"TOKEN=x"}, "must not be shipped"),
        ({"../evil.txt": b"x"}, "unsafe or misplaced"),
        ({"Other/file.txt": b"x"}, "unsafe or misplaced"),
        ({"C:/Windows/x.dll": b"x"}, "unsafe or misplaced"),
    ],
)
def test_bad_archives_fail_closed_without_a_receipt(
    fx: Fixture, overrides: dict[str, bytes | None], message: str
) -> None:
    archive = fx.build(overrides)
    with pytest.raises(VerificationError, match=message):
        verify(fx, archive)
    assert not (fx.tmp_path / "receipt.json").exists()


def test_name_version_and_existing_output_are_enforced(fx: Fixture) -> None:
    with pytest.raises(VerificationError, match="does not match the expected version"):
        verify(fx, fx.build(), expected_version="0.0.1")
    with pytest.raises(VerificationError, match="Archive name"):
        verify(fx, fx.build(name="something-else.zip"))
    archive = fx.build()
    (fx.tmp_path / "receipt.json").write_text("{}", "utf-8")
    with pytest.raises(VerificationError, match="already exists"):
        verify(fx, archive)


def test_not_a_zip_and_corrupt_archive_are_rejected(fx: Fixture) -> None:
    broken = fx.tmp_path / f"DetourVpn-windows-x64-{VERSION}.zip"
    broken.write_bytes(b"not a zip at all")
    with pytest.raises(VerificationError, match="not a valid zip"):
        verify(fx, broken)


def test_pe_reader_reports_what_the_verifier_relies_on() -> None:
    info = read_pe(fake_pe(version="1.2.3", admin=True))
    assert info["machine"] == 0x8664 and info["product_version"] == "1.2.3"
    assert info["requires_administrator"] is True and info["signed"] is False
    with pytest.raises(VerificationError):
        read_pe(b"not a pe file" * 40)


def test_cli_returns_failure_code_and_no_path_in_the_message(
    fx: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = fx.build({f"{ROOT}/wintun.dll": None})
    code = main(
        [
            "--zip",
            str(archive),
            "--output",
            str(fx.tmp_path / "r.json"),
            "--repo-root",
            str(fx.repo),
        ]
    )
    assert code == 1
    assert str(fx.tmp_path) not in capsys.readouterr().err
    good = fx.build()
    assert (
        main(
            [
                "--zip",
                str(good),
                "--output",
                str(fx.tmp_path / "ok.json"),
                "--repo-root",
                str(fx.repo),
            ]
        )
        == 0
    )

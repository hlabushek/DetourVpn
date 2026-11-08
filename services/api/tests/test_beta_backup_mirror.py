"""Backup mirror tests use synthetic ciphertext and a fake fetch command; no network."""

import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import beta_backup_mirror as mirror  # noqa: E402

T0 = datetime(2026, 11, 8, 4, 30, tzinfo=UTC)


def blob(tag: str = "a") -> bytes:
    return b"gAAAAA" + tag.encode() * 40


def fake_command(tmp_path: Path, payload: bytes, status: int = 0) -> list[str]:
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    code = (
        "import sys; sys.stdout.buffer.write(open("
        + repr(str(source))
        + ", 'rb').read()); sys.exit("
        + str(status)
        + ")"
    )
    return [sys.executable, "-c", code]


def make_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "mirror"
    directory.mkdir(mode=0o700)
    return directory


def test_stores_new_archive_with_private_mode(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    result = mirror.run(directory, fake_command(tmp_path, blob()), 14, 1, 0, 30, T0)
    assert result["status"] == "stored" and result["kept"] == 1
    (path,) = list(directory.iterdir())
    assert mirror.NAME.match(path.name)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == blob()


def test_identical_archive_is_not_duplicated_and_goes_stale(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    command = fake_command(tmp_path, blob())
    mirror.run(directory, command, 14, 1, 0, 30, T0)
    again = mirror.run(directory, command, 14, 1, 0, 30, T0 + timedelta(hours=24))
    assert again["status"] == "unchanged" and len(list(directory.iterdir())) == 1
    with pytest.raises(mirror.MirrorError, match="source_stale"):
        mirror.run(directory, command, 14, 1, 0, 30, T0 + timedelta(hours=49))


def test_retention_keeps_only_newest(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    for day in range(5):
        command = fake_command(tmp_path, blob(chr(ord("a") + day)))
        mirror.run(directory, command, 3, 1, 0, 30, T0 + timedelta(days=day))
    names = sorted(path.name for path in directory.iterdir())
    assert len(names) == 3
    assert names[0].startswith("beta-20261110")  # the two oldest were pruned


def test_rejects_plaintext_empty_and_failed_fetch(tmp_path: Path) -> None:
    directory = make_dir(tmp_path)
    with pytest.raises(mirror.MirrorError, match="not_encrypted_archive"):
        mirror.run(directory, fake_command(tmp_path, b"PGDMP plain"), 14, 1, 0, 30, T0)
    with pytest.raises(mirror.MirrorError, match="size_invalid"):
        mirror.run(directory, fake_command(tmp_path, b""), 14, 1, 0, 30, T0)
    with pytest.raises(mirror.MirrorError, match="fetch_failed"):
        mirror.run(directory, fake_command(tmp_path, blob(), status=1), 14, 2, 0, 30, T0)
    assert list(directory.iterdir()) == []


def test_oversize_is_rejected_without_storing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = make_dir(tmp_path)
    monkeypatch.setattr(mirror, "MAX_BYTES", 100)
    with pytest.raises(mirror.MirrorError, match="size_invalid"):
        mirror.run(directory, fake_command(tmp_path, blob("z") * 5), 14, 1, 0, 30, T0)
    assert list(directory.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_unsafe_destination_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "open"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(mirror.MirrorError, match="destination_unsafe"):
        mirror.run(directory, fake_command(tmp_path, blob()), 14, 1, 0, 30, T0)


def test_ssh_command_pins_host_key_and_rejects_bad_host(tmp_path: Path) -> None:
    command = mirror.ssh_command("203.0.113.9", tmp_path / "id", tmp_path / "kh")
    assert "StrictHostKeyChecking=yes" in command and "BatchMode=yes" in command
    assert command[-1] == "root@203.0.113.9"
    with pytest.raises(mirror.MirrorError, match="host_invalid"):
        mirror.ssh_command("host; rm -rf /", tmp_path / "id", tmp_path / "kh")

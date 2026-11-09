"""Pull the encrypted beta backup onto a second host and keep a bounded history.

Runs on the beta node as an unprivileged user. It holds no decryption key: it can only
copy the ciphertext and check its shape. The remote side is restricted by SSH to one
fixed read-only command, so this host cannot choose what to read.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
FERNET_PREFIX = b"gAAAAA"
NAME = re.compile(r"^beta-(\d{8}T\d{6}Z)-([0-9a-f]{16})\.enc$")
DEFAULT_KEEP = 7
STALE_AFTER = timedelta(hours=48)
HOST = re.compile(r"^[0-9A-Za-z.:-]{3,64}$")


class MirrorError(Exception):
    """Only stable codes may reach the journal."""


def bounded_fetch(command: list[str], timeout: int) -> bytes:
    with subprocess.Popen(  # noqa: S603 - fixed argv, no shell, key path is operator-owned.
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
    ) as process:
        try:
            if process.stdout is None:
                raise MirrorError("fetch_failed")
            data = process.stdout.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                process.kill()
                raise MirrorError("size_invalid")
            if process.wait(timeout=timeout) != 0:
                raise MirrorError("fetch_failed")
        except subprocess.TimeoutExpired:
            process.kill()
            raise MirrorError("fetch_timeout") from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if not data:
        raise MirrorError("size_invalid")
    if not data.startswith(FERNET_PREFIX):
        raise MirrorError("not_encrypted_archive")
    return data


def listing(directory: Path) -> list[tuple[str, str, Path]]:
    found = []
    for entry in directory.iterdir():
        match = NAME.match(entry.name)
        if match and entry.is_file() and not entry.is_symlink():
            found.append((match.group(1), match.group(2), entry))
    return sorted(found)


def check_directory(directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise MirrorError("destination_invalid")
    if sys.platform == "win32":  # Windows has no meaningful POSIX owner/mode bits.
        return
    info = directory.stat()
    if info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise MirrorError("destination_unsafe")


def store(directory: Path, data: bytes, now: datetime, keep: int) -> dict[str, object]:
    if keep < 1:
        raise MirrorError("keep_invalid")
    check_directory(directory)
    short = hashlib.sha256(data).hexdigest()[:16]
    existing = listing(directory)
    same = [item for item in existing if item[1] == short]
    if same:
        # The source did not change. A source that stays unchanged for days has stopped
        # producing archives, which the owner must see.
        stamp = datetime.strptime(same[-1][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if now - stamp > STALE_AFTER:
            raise MirrorError("source_stale")
        return {"status": "unchanged", "kept": len(existing), "bytes": len(data)}

    name = f"beta-{now.strftime('%Y%m%dT%H%M%SZ')}-{short}.enc"
    target = directory / name
    staged = directory / f".{name}.part"
    try:
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(staged, target)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    items = listing(directory)
    for _stamp, _digest, path in items[:-keep]:
        path.unlink()
    return {"status": "stored", "kept": min(len(items), keep), "bytes": len(data)}


def run(
    directory: Path,
    command: list[str],
    keep: int,
    attempts: int,
    pause: float,
    timeout: int,
    now: datetime | None = None,
) -> dict[str, object]:
    last = MirrorError("fetch_failed")
    for attempt in range(attempts):
        try:
            data = bounded_fetch(command, timeout)
            return store(directory, data, now or datetime.now(UTC), keep)
        except MirrorError as error:
            last = error
            if str(error) not in {"fetch_failed", "fetch_timeout"} or attempt + 1 == attempts:
                break
            time.sleep(pause * (attempt + 1))
    raise last


def ssh_command(host: str, key: Path, known_hosts: Path) -> list[str]:
    if not HOST.match(host):
        raise MirrorError("host_invalid")
    return [
        "ssh",
        "-F", "/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=20",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "IdentitiesOnly=yes",
        "-i", str(key),
        f"root@{host}",
    ]  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--pause", type=float, default=20.0)
    arguments = parser.parse_args()
    try:
        command = ssh_command(arguments.host, arguments.key, arguments.known_hosts)
        result = run(
            arguments.dest, command, arguments.keep, arguments.attempts, arguments.pause, 120
        )
    except MirrorError as error:
        print(json.dumps({"code": "mirror_failed", "reason": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

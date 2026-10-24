"""Root-private, exclusively locked Linux node journal opener; stdlib only."""

import os
import sqlite3
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class RuntimeFault(Exception):
    """Fixed diagnostics only."""


@contextmanager
def open_private_journal(path: Path) -> Iterator[sqlite3.Connection]:
    """Production opener. Root-only pre-created private directory outside checkout."""
    if sys.platform != "linux":
        raise RuntimeFault("private_linux_root_journal_required")
    import fcntl

    if os.geteuid() != 0:
        raise RuntimeFault("private_linux_root_journal_required")
    parent = path.parent
    for item in (parent, *parent.parents):
        info = item.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeFault("journal_directory_untrusted")
    if parent.stat().st_mode & 0o077:
        raise RuntimeFault("journal_directory_not_private")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise RuntimeFault("journal_file_untrusted") from None
    else:
        os.fsync(fd)
        os.close(fd)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    try:
        lock = os.open(
            path.with_suffix(".lock"),
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
    except OSError:
        raise RuntimeFault("journal_lock_untrusted") from None
    connection = None
    try:
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise RuntimeFault("journal_lock_untrusted")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeFault("journal_already_running") from None
        connection = sqlite3.connect(path, timeout=5)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        os.close(lock)

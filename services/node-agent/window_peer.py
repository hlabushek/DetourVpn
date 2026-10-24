"""Fixed single-peer AWG operation per isolated operator slot. No process launcher."""

import base64
import hashlib
import os
import stat
import subprocess
from pathlib import Path

AWG_HASH = "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce"
CORE_HASH = "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LOG_LEVEL": "silent"}


class PeerFault(Exception):
    """Fixed diagnostics only; no native output or key echo."""


def public(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise PeerFault("invalid_public_key") from None
    if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
        raise PeerFault("invalid_public_key")
    return value


class SlotPeer:
    def __init__(
        self,
        slot_id: int,
        awg: Path,
        core_pid: int,
        namespace_fd: int,
        server_public_key: str,
    ) -> None:
        if type(slot_id) is not int or not 1 <= slot_id <= 16:
            raise PeerFault("invalid_operator_slot")
        if type(core_pid) is not int or core_pid <= 1:
            raise PeerFault("invalid_core_process")
        self.slot_id, self.awg, self.pid = slot_id, awg, core_pid
        self.interface, self.port = f"dv3s{slot_id}", 51900 + slot_id
        self.public_key = public(server_public_key)
        self.fd = os.dup(namespace_fd)
        try:
            self.validate()
        except BaseException:
            os.close(self.fd)
            raise

    def dispose(self) -> None:
        os.close(self.fd)

    def command(self, arguments: list[str]) -> str:
        result = subprocess.run(  # noqa: S603 - pinned executable, fixed verbs/scalars, no shell
            [str(self.awg), *arguments],
            capture_output=True,
            text=True,
            env=ENV,
            timeout=5,
            check=False,
        )
        if result.returncode:
            raise PeerFault("native_peer_operation_failed")
        return result.stdout.strip()

    def validate(self) -> None:
        for item in (self.awg, *self.awg.parents):
            info = item.lstat()
            if info.st_uid != 0 or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
                raise PeerFault("operator_artifact_untrusted")
        process = Path("/proc") / str(self.pid)
        inode = os.fstat(self.fd).st_ino
        if (
            process.stat().st_uid != 0
            or inode == Path("/proc/self/ns/net").stat().st_ino
            or (process / "ns/net").stat().st_ino != inode
            or hashlib.sha256(self.awg.read_bytes()).hexdigest() != AWG_HASH
            or hashlib.sha256((process / "exe").read_bytes()).hexdigest() != CORE_HASH
        ):
            raise PeerFault("operator_core_identity_invalid")
        if self.command(["show", self.interface, "public-key"]) != self.public_key:
            raise PeerFault("operator_server_key_conflict")
        if self.command(["show", self.interface, "listen-port"]) != str(self.port):
            raise PeerFault("operator_server_port_conflict")

    def inventory(self) -> list[str]:
        self.validate()
        peers = self.command(["show", self.interface, "peers"]).splitlines()
        if len(peers) > 1:
            raise PeerFault("operator_slot_inventory_conflict")
        return [public(value) for value in peers]

    def install(self, public_key: str) -> None:
        public_key = public(public_key)
        peers = self.inventory()
        if peers and peers != [public_key]:
            raise PeerFault("operator_slot_occupied")
        self.command(["set", self.interface, "peer", public_key, "allowed-ips", "10.203.71.2/32"])
        if self.inventory() != [public_key]:
            raise PeerFault("native_peer_not_applied")

    def remove(self) -> None:
        for public_key in self.inventory():
            self.command(["set", self.interface, "peer", public_key, "remove"])
        if self.inventory():
            raise PeerFault("native_peer_not_removed")

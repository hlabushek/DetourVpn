"""ADR-101 pool mode: many peers on the fixed slot-1 AWG interface of one operator namespace.

Each peer index ``n`` (1..250) owns the fixed inner address ``10.203.72.(n+1)``. WireGuard's
cryptokey routing binds that address to exactly one public key (``allowed-ips n/32``), so the
inner source address is authenticated. Each peer gets its own nftables table on the forward
hook that admits and meters only its address, in both directions, while its window is live.
Every base chain must accept a packet, so a table only ever constrains its own peer.

Not a remote shell: commands, verbs, interface, namespace and addresses are fixed or derived
from validated integers. No native output or keys are echoed.
"""

import hashlib
import ipaddress
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from window_gate import ENV as NFT_ENV
from window_gate import MAX_WINDOW_MS, NFT, NSENTER, GateError
from window_peer import AWG_HASH, CORE_HASH, PeerFault, public

INTERFACE = "dv3s1"
PORT = 51901
NETWORK = ipaddress.IPv4Network("10.203.72.0/24")
MAX_PEERS = 250
AWG_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LOG_LEVEL": "silent"}
VALIDATE_EVERY_S = 10.0
LIVE_CACHE_S = 0.5


def peer_address(peer: int) -> str:
    if type(peer) is not int or not 1 <= peer <= MAX_PEERS:
        raise PeerFault("invalid_pool_peer")
    return str(NETWORK.network_address + peer + 1)


class PoolInterface:
    """Shared, pinned view of the pool namespace; validation and live state are cached."""

    def __init__(self, awg: Path, core_pid: int, namespace_fd: int, server_public_key: str) -> None:
        if type(core_pid) is not int or core_pid <= 1:
            raise PeerFault("invalid_core_process")
        self.awg, self.pid = awg, core_pid
        self.public_key = public(server_public_key)
        self.fd = os.dup(namespace_fd)
        self._validated_at = float("-inf")
        self._live_at = float("-inf")
        self._live: set[str] = set()
        try:
            if os.fstat(self.fd).st_ino == os.stat("/proc/self/ns/net").st_ino:
                raise GateError("host_namespace_forbidden")
            self.validate(force=True)
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
            env=AWG_ENV,
            timeout=5,
            check=False,
        )
        if result.returncode:
            raise PeerFault("native_peer_operation_failed")
        return result.stdout.strip()

    def nft(self, arguments: list[str], program: str | None = None) -> str:
        result = subprocess.run(  # noqa: S603 - fixed executable/verbs, pinned fd, no shell
            [NSENTER, f"--net=/proc/self/fd/{self.fd}", "--", NFT, *arguments],
            input=program,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
            pass_fds=(self.fd,),
            env=NFT_ENV,
        )
        if result.returncode:
            raise GateError("kernel_gate_operation_failed")
        return result.stdout

    def validate(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._validated_at < VALIDATE_EVERY_S:
            return
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
        if self.command(["show", INTERFACE, "public-key"]) != self.public_key:
            raise PeerFault("operator_server_key_conflict")
        if self.command(["show", INTERFACE, "listen-port"]) != str(PORT):
            raise PeerFault("operator_server_port_conflict")
        self._validated_at = now

    def allowed(self) -> dict[str, list[str]]:
        """Public key -> allowed inner addresses, as installed on the interface."""
        self.validate()
        result: dict[str, list[str]] = {}
        for line in self.command(["show", INTERFACE, "allowed-ips"]).splitlines():
            parts = line.split()
            if not parts:
                continue
            key = public(parts[0])
            ips = [] if parts[1:] == ["(none)"] else parts[1:]
            for value in ips:
                network = ipaddress.ip_network(value, strict=True)
                if network.prefixlen != 32 or network.network_address not in NETWORK:
                    raise PeerFault("pool_inventory_conflict")
            result[key] = [str(ipaddress.ip_network(v).network_address) for v in ips]
        return result

    def live_tables(self) -> set[str]:
        """Names of gate tables whose live set currently has an element (one read per tick)."""
        now = time.monotonic()
        if now - self._live_at >= LIVE_CACHE_S:
            ruleset = json.loads(self.nft(["-j", "list", "ruleset"]))
            self._live = {
                row["set"]["table"]
                for row in ruleset.get("nftables", [])
                if row.get("set", {}).get("name") == "live" and row["set"].get("elem")
            }
            self._live_at = now
        return self._live

    def invalidate_live(self) -> None:
        self._live_at = float("-inf")


class PoolPeer:
    """Peer protocol for one pool index; installs exactly one key on its fixed address."""

    def __init__(self, peer: int, interface: PoolInterface) -> None:
        self.peer, self.address, self.interface = peer, peer_address(peer), interface

    def holders(self) -> list[str]:
        return [key for key, ips in self.interface.allowed().items() if self.address in ips]

    def install(self, public_key: str) -> None:
        public_key = public(public_key)
        installed = self.interface.allowed()
        holders = [key for key, ips in installed.items() if self.address in ips]
        if holders and holders != [public_key]:
            raise PeerFault("operator_slot_occupied")
        if installed.get(public_key, [self.address]) not in ([], [self.address]):
            raise PeerFault("pool_peer_key_conflict")  # Same key already bound elsewhere.
        self.interface.command(
            ["set", INTERFACE, "peer", public_key, "allowed-ips", f"{self.address}/32"]
        )
        if self.holders() != [public_key]:
            raise PeerFault("native_peer_not_applied")

    def remove(self) -> None:
        for public_key in self.holders():
            self.interface.command(["set", INTERFACE, "peer", public_key, "remove"])
        if self.holders():
            raise PeerFault("native_peer_not_removed")


class PoolGate:
    """Gate protocol for one pool index: per-address deadline, byte budget and meter."""

    def __init__(self, peer: int, interface: PoolInterface) -> None:
        self.address, self.interface = peer_address(peer), interface
        self.table = f"detour_pool_p{peer}"

    def program(self, budget: int, milliseconds: int, *, replace: bool) -> str:
        if type(budget) is not int or not 65536 <= budget <= 67108864:
            raise GateError("invalid_window_budget")
        if type(milliseconds) is not int or not 0 <= milliseconds <= MAX_WINDOW_MS:
            raise GateError("invalid_window_duration")
        live = f"elements = {{ {self.address} timeout {milliseconds}ms }};" if milliseconds else ""
        prefix = f"delete table inet {self.table}\n" if replace else ""
        a, i = self.address, INTERFACE
        return (
            prefix
            + f"""table inet {self.table} {{
 quota budget {{ until {budget} bytes; }}
 counter passed {{ }}
 set live {{ type ipv4_addr; flags timeout; {live} }}
 chain forward {{ type filter hook forward priority -50; policy accept;
  iifname "{i}" ip saddr {a} ip saddr @live quota name budget counter name passed accept
  iifname "{i}" ip saddr {a} drop
  oifname "{i}" ip daddr {a} ip daddr @live quota name budget counter name passed accept
  oifname "{i}" ip daddr {a} drop
 }}
}}
"""
        )

    def _apply(self, arguments: list[str], program: str | None = None) -> str:
        try:
            return self.interface.nft(arguments, program)
        finally:
            self.interface.invalidate_live()

    def initialize_closed(self) -> bool:
        tables = json.loads(self.interface.nft(["-j", "list", "tables"]))
        exists = any(
            row.get("table", {}).get("family") == "inet"
            and row.get("table", {}).get("name") == self.table
            for row in tables["nftables"]
        )
        if exists:
            self.close()
        else:
            self._apply(["-f", "-"], self.program(65536, 0, replace=False))
        return exists

    def is_live(self) -> bool:
        return self.table in self.interface.live_tables()

    def install_closed(self, budget: int) -> None:
        """Reset only after the caller durably captured the old final receipt."""
        self._apply(["-f", "-"], self.program(budget, 0, replace=True))

    def open(self, milliseconds: int) -> None:
        """One-shot; the runtime enforces generation/replay/deadline (never refreshes a TTL)."""
        if type(milliseconds) is not int or not 1 <= milliseconds <= MAX_WINDOW_MS:
            raise GateError("invalid_window_duration")
        self._apply(
            ["-f", "-"],
            f"add element inet {self.table} live {{ {self.address} timeout {milliseconds}ms }}\n",
        )

    def close(self) -> None:
        self._apply(["flush", "set", "inet", self.table, "live"])

    def closed_bytes(self) -> int:
        """Only a closed gate is evidence of a final, immutable counter snapshot."""
        result = json.loads(self.interface.nft(["-j", "list", "table", "inet", self.table]))
        live = [
            row["set"] for row in result["nftables"] if row.get("set", {}).get("name") == "live"
        ]
        counters = [
            row["counter"]
            for row in result["nftables"]
            if row.get("counter", {}).get("name") == "passed"
        ]
        if len(live) != 1 or live[0].get("elem") or len(counters) != 1:
            raise GateError("final_counter_requires_closed_gate")
        amount = counters[0].get("bytes")
        if type(amount) is not int or not 0 <= amount <= 67108864:
            raise GateError("kernel_meter_invalid")
        return amount

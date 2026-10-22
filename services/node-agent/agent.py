"""Bounded single-peer staging daemon. Fixed native operations, no remote shell API.

Run only inside the reviewed staging service. Credentials are read from a private
runtime file, never from arguments. Network traffic goes through an SSH-protected
loopback reverse forwarding endpoint; no public plaintext HTTP transport.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import os
import signal
import stat
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path("/run/project-vpn-i012-agent")
AWG = Path("/opt/project-vpn/current/awg")
AWG_HASH = "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce"
CORE_HASH = "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe"
ENDPOINT_READY = Path("/run/project-vpn-i011/ready.json")
PROC = Path("/proc")
ENDPOINT_CGROUP = "0::/system.slice/project-vpn-i011.service"
IP = "/usr/sbin/ip"
NFT = "/usr/sbin/nft"
NS = "vpn-i011"
INTERFACE = "v11awg"
PORT = 51892
TABLE = "vpn_i012"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LOG_LEVEL": "silent"}
AGENT_STAGE = "initialize"


class AgentFault(Exception):
    """Fixed diagnostics only; command output, headers and config stay private."""


def private_path(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
            raise AgentFault("untrusted_path")


def command(argv: list[str], data: str | None = None, *, pass_fds: tuple[int, ...] = ()) -> str:
    result = subprocess.run(  # noqa: S603 - fixed verbs and validated scalar arguments
        argv,
        input=data,
        capture_output=True,
        text=True,
        env=ENV,
        timeout=5,
        check=False,
        pass_fds=pass_fds,
    )
    if result.returncode:
        raise AgentFault("native_operation_failed")
    return result.stdout


def open_endpoint_namespace() -> int:
    """Pin the real core netns, never the placeholder outside its private mount namespace."""
    global AGENT_STAGE
    AGENT_STAGE = "namespace_identity"
    private_path(ENDPOINT_READY)
    if not ENDPOINT_READY.is_file() or ENDPOINT_READY.stat().st_size > 1024:
        raise AgentFault("endpoint_identity_invalid")
    ready = json.loads(ENDPOINT_READY.read_text())
    pid, inode = ready.get("native_pid"), ready.get("native_netns_inode")
    if ready.get("endpoint_ready") is not True or type(pid) is not int or pid <= 1:
        raise AgentFault("endpoint_identity_invalid")
    if type(inode) is not int or inode <= 0:
        raise AgentFault("endpoint_identity_invalid")
    process = PROC / str(pid)
    fd = os.open(process / "ns/net", os.O_RDONLY)  # Non-inheritable by default (PEP 446).
    try:
        if (
            process.stat().st_uid != 0
            or (process / "cgroup").read_text().strip() != ENDPOINT_CGROUP
            or hashlib.sha256((process / "exe").read_bytes()).hexdigest() != CORE_HASH
            or os.fstat(fd).st_ino != inode
            or (process / "ns/net").stat().st_ino != inode
            or (PROC / "self/ns/net").stat().st_ino == inode
        ):
            raise AgentFault("endpoint_identity_invalid")
        return fd
    except BaseException:
        os.close(fd)
        raise


def key(value: object) -> str:
    if not isinstance(value, str):
        raise AgentFault("invalid_public_key")
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raise AgentFault("invalid_public_key") from None
    if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
        raise AgentFault("invalid_public_key")
    return value


class Native:
    def __init__(self, expected_public_key: str, *, namespace_fd: int | None = None) -> None:
        self.public_key = key(expected_public_key)
        self.namespace_fd = namespace_fd

    def nft(self, args: list[str], data: str | None = None) -> str:
        if self.namespace_fd is None:
            # Local lab compatibility only. Production main always supplies a pinned fd.
            return command([IP, "netns", "exec", NS, NFT, *args], data)
        return command(
            ["/usr/bin/nsenter", f"--net=/proc/self/fd/{self.namespace_fd}", "--", NFT, *args],
            data,
            pass_fds=(self.namespace_fd,),
        )

    def validate(self) -> None:
        private_path(AWG.resolve())
        if hashlib.sha256(AWG.read_bytes()).hexdigest() != AWG_HASH:
            raise AgentFault("binary_hash_mismatch")
        if command([str(AWG), "show", INTERFACE, "public-key"]).strip() != self.public_key:
            raise AgentFault("server_key_mismatch")
        if command([str(AWG), "show", INTERFACE, "listen-port"]).strip() != str(PORT):
            raise AgentFault("server_port_mismatch")

    def peers(self) -> list[str]:
        return [key(value) for value in command([str(AWG), "show", INTERFACE, "peers"]).split()]

    def counters(self, public_key: str) -> tuple[int, int]:
        for line in command([str(AWG), "show", INTERFACE, "transfer"]).splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == public_key:
                rx, tx = int(parts[1]), int(parts[2])
                if rx < 0 or tx < 0 or rx + tx > 2**63 - 1:
                    raise AgentFault("invalid_native_counters")
                return rx, tx
        raise AgentFault("native_counters_missing")

    def gate(self, budget: int, seconds: int) -> None:
        if type(budget) is not int or not 65536 <= budget <= 16777216 or not 1 <= seconds <= 60:
            raise AgentFault("invalid_lease")
        # Table must already exist with a closed gate. Replace atomically, never flush ruleset.
        program = f"""delete table inet {TABLE}
table inet {TABLE} {{
 quota lease_bytes {{ until {budget} bytes; }}
 set live {{ type inet_service; flags timeout; elements = {{ {PORT} timeout {seconds}s }}; }}
 chain inbound {{ type filter hook input priority -50; policy accept;
  udp dport {PORT} udp dport @live quota name lease_bytes accept
  udp dport {PORT} drop
 }}
 chain outbound {{ type filter hook output priority -50; policy accept;
  udp sport {PORT} udp sport @live quota name lease_bytes accept
  udp sport {PORT} drop
 }}
}}
"""
        self.nft(["-f", "-"], program)

    def initialize_gate(self) -> None:
        # Only the dedicated namespace/table is touched. Existing host rules are untouched.
        tables = json.loads(self.nft(["-j", "list", "tables"]))
        exists = any(item.get("table", {}).get("name") == TABLE for item in tables["nftables"])
        if not exists:
            self.nft(
                ["-f", "-"],
                f"table inet {TABLE} {{ set live {{ type inet_service; flags timeout; }}\n"
                "chain inbound { type filter hook input priority -50; policy accept; "
                f"udp dport {PORT} drop; }}\n"
                "chain outbound { type filter hook output priority -50; policy accept; "
                f"udp sport {PORT} drop; }}\n}}\n",
            )
        else:
            self.close_gate()

    def close_gate(self) -> None:
        self.nft(["flush", "set", "inet", TABLE, "live"])

    def add(self, public_key: str) -> None:
        command(
            [str(AWG), "set", INTERFACE, "peer", key(public_key), "allowed-ips", "10.203.71.2/32"]
        )
        if self.peers() != [public_key]:
            raise AgentFault("peer_not_applied")

    def remove(self, public_key: str) -> None:
        command([str(AWG), "set", INTERFACE, "peer", key(public_key), "remove"])
        if public_key in self.peers():
            raise AgentFault("peer_not_removed")


class Agent:
    def __init__(self, native: Native, journal: Path) -> None:
        self.native, self.journal = native, journal
        self.records: dict[str, Any] = json.loads(journal.read_text()) if journal.exists() else {}
        if len(self.records) > 1000:
            raise AgentFault("journal_capacity")

    def save(self) -> None:
        temporary = self.journal.with_name("journal-" + uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            json.dump(self.records, output, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.journal)

    def startup(self) -> None:
        global AGENT_STAGE
        AGENT_STAGE = "validate_native"
        self.native.validate()
        AGENT_STAGE = "initialize_gate"
        self.native.initialize_gate()
        # A process restart never restores an add or resets its budget. Preserve final
        # counters before deleting peers; unknown/reset counters are charged conservatively.
        AGENT_STAGE = "read_inventory"
        present = self.native.peers()
        captured: set[str] = set()
        for public_key in present:
            rows = [
                row
                for row in self.records.values()
                if row["public_key"] == public_key and row["status"] == "active"
            ]
            if len(rows) == 1:
                try:
                    rows[0]["rx"], rows[0]["tx"] = self.native.counters(public_key)
                    rows[0]["lost"] = False
                    captured.add(public_key)
                except AgentFault:
                    rows[0]["lost"] = True
            AGENT_STAGE = "remove_bootstrap_peer"
            self.native.remove(public_key)
        for record in self.records.values():
            if record["status"] != "removed":
                record["status"] = "removed"
                if record["public_key"] not in captured:
                    record["lost"] = True
        AGENT_STAGE = "persist_journal"
        self.save()
        AGENT_STAGE = "running"

    def execute(self, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {
            "command_id",
            "peer_id",
            "peer_version",
            "operation",
            "public_key",
            "lease_deadline",
            "budget_bytes",
        }:
            raise AgentFault("invalid_command")
        command_id, peer_id = str(UUID(body["command_id"])), str(UUID(body["peer_id"]))
        public_key = key(body["public_key"])
        version, budget = body["peer_version"], body["budget_bytes"]
        if type(version) is not int or not 1 <= version <= 2**63 - 1 or type(budget) is not int:
            raise AgentFault("invalid_command")
        deadline = datetime.fromisoformat(body["lease_deadline"])
        if deadline.tzinfo is None:
            raise AgentFault("invalid_command")
        remaining = math.floor((deadline - datetime.now(UTC)).total_seconds())
        record = self.records.get(peer_id)
        ack: dict[str, Any] = {
            "contract_version": 2,
            "command_id": command_id,
            "peer_id": peer_id,
            "peer_version": version,
            "operation": body["operation"],
            "server_public_key": self.native.public_key,
        }
        if body["operation"] == "add_peer":
            if record is not None:
                ack["applied"] = (
                    record["status"] == "active"
                    and record["version"] == version
                    and record["public_key"] == public_key
                    and record["deadline"] == body["lease_deadline"]
                    and record["budget"] == budget
                    and remaining > 0
                    and self.native.peers() == [public_key]
                )
                return ack
            record = {
                "public_key": public_key,
                "version": version,
                "status": "intent",
                "rx": 0,
                "tx": 0,
                "lost": False,
                "deadline": body["lease_deadline"],
                "budget": budget,
            }
            self.records[peer_id] = record
            self.save()  # Durable intent BEFORE resetting any kernel budget or native counters.
            if not 1 <= remaining <= 60 or not 65536 <= budget <= 16777216 or self.native.peers():
                record["status"] = "removed"
                self.save()
                ack["applied"] = False
                return ack
            try:
                self.native.gate(budget, remaining)
                self.native.add(public_key)
            except Exception:
                self.native.close_gate()
                self.native.remove(public_key)
                record["status"], record["lost"] = "removed", True
                self.save()
                raise AgentFault("peer_apply_failed") from None
            record["status"] = "active"
            self.save()
            ack["applied"] = True
        elif body["operation"] == "remove_peer":
            self.native.close_gate()
            if record is None:
                record = {
                    "public_key": public_key,
                    "version": version,
                    "status": "removed",
                    "rx": 0,
                    "tx": 0,
                    "lost": True,
                    "deadline": body["lease_deadline"],
                    "budget": budget,
                }
                self.records[peer_id] = record
            if record["public_key"] != public_key or version < record["version"]:
                raise AgentFault("peer_version_conflict")
            if record["status"] != "removed":
                try:
                    record["rx"], record["tx"] = self.native.counters(public_key)
                except AgentFault:
                    record["lost"] = True
            self.native.remove(public_key)
            record["status"], record["version"] = "removed", version
            self.save()
            ack.update(rx_bytes=record["rx"], tx_bytes=record["tx"], counters_lost=record["lost"])
        else:
            raise AgentFault("unknown_operation")
        return ack

    def expire(self) -> None:
        for record in self.records.values():
            if record["status"] == "active" and datetime.fromisoformat(
                record["deadline"]
            ) <= datetime.now(UTC):
                self.native.close_gate()
                try:
                    record["rx"], record["tx"] = self.native.counters(record["public_key"])
                except AgentFault:
                    record["lost"] = True
                self.native.remove(record["public_key"])
                record["status"] = "removed"
                self.save()


def request(config: dict[str, Any], suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
    # Fixed loopback endpoint, protected by owner-approved SSH reverse forwarding.
    connection = http.client.HTTPConnection("127.0.0.1", 18765, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/internal/v1/agent/peers/lifecycle/" + suffix,
            body=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Node-Id": str(UUID(config["node_id"])),
                "X-Agent-Credential": config["credential"],
                "X-Agent-Timestamp": str(int(time.time())),
                "X-Agent-Nonce": str(uuid4()),
            },
        )
        response = connection.getresponse()
        raw = response.read(16385)
        if response.status != 200 or len(raw) > 16384:
            raise AgentFault("control_plane_unavailable")
        result: dict[str, Any] = json.loads(raw)
        return result
    finally:
        connection.close()


def main() -> int:
    import fcntl  # Linux daemon only; import stays inside entrypoint for Windows contract tests.

    if os.geteuid() != 0:  # type: ignore[attr-defined]  # Linux-only daemon entrypoint.
        raise AgentFault("staging_privilege_required")
    private_path(ROOT)
    configuration = ROOT / "config.json"
    private_path(configuration)
    if configuration.stat().st_mode & 0o077 or configuration.stat().st_size > 4096:
        raise AgentFault("private_config_required")
    config = json.loads(configuration.read_text())
    UUID(config["runtime_id"])
    lock = (ROOT / "lock").open("a")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
    namespace_fd = open_endpoint_namespace()
    agent = Agent(
        Native(config["server_public_key"], namespace_fd=namespace_fd), ROOT / "journal.json"
    )
    agent.startup()
    running = True

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            agent.expire()
            try:
                response = request(
                    config,
                    "poll",
                    {
                        "contract_version": 2,
                        "capabilities": ["peer-lease.v2", "kernel-udp-budget.v1"],
                        "server_public_key": agent.native.public_key,
                        "runtime_id": config["runtime_id"],
                        "observed_public_keys": agent.native.peers(),
                    },
                )
                for item in response["commands"]:
                    request(config, "ack", agent.execute(item))
            except (OSError, ValueError, http.client.HTTPException, AgentFault):
                # Kernel gate remains the deadline/budget authority, even on network failure.
                pass
            time.sleep(0.5)
    finally:
        agent.native.close_gate()
        agent.startup()
        lock.close()
        os.close(namespace_fd)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"error": "node_agent_failed", "stage": AGENT_STAGE}), flush=True)
        raise SystemExit(1) from None

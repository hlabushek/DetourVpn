"""Fixed v3 node daemon. Install only via a separately approved operator rollout.

Requires root-owned artifacts, already running fixed slot cores and private ready
files. Does not install/launch cores, configure host routing, or execute remote shell.
"""

import json
import os
import re
import signal
import ssl
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import Field, field_validator
from window_gate import SlotGate
from window_peer import SlotPeer

from vpn_api.tunnels.v3_contracts import CAPABILITIES, ContractError, FrozenInput, WindowCommands
from vpn_api.tunnels.window_journal import RuntimeFault, open_private_journal
from vpn_api.tunnels.window_runtime import Gate, Peer, WindowJournal, WindowRuntime

CONFIG = Path("/etc/detourvpn-node/agent.json")
CREDENTIAL = Path("/etc/detourvpn-node/credential")
STATE = Path("/var/lib/detourvpn-node/state.sqlite")
READY = Path("/run/detourvpn-node/slots")
AWG = Path("/opt/detourvpn-node/current/awg")
STOP = False


class Configuration(FrozenInput):
    node_id: UUID
    api_origin: str
    slot_count: int = Field(strict=True, ge=1, le=16)

    @field_validator("api_origin")
    @classmethod
    def origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Protected HTTPS control origin required")
        return value.rstrip("/")


class ReadySlot(FrozenInput):
    slot_id: int = Field(strict=True, ge=1, le=16)
    native_pid: int = Field(strict=True, gt=1)
    native_netns_inode: int = Field(strict=True, gt=0)
    server_public_key: str = Field(min_length=44, max_length=44, repr=False)


def private_read(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o077
        or info.st_nlink != 1
        or info.st_size > limit
    ):
        raise RuntimeFault("private_node_file_untrusted")
    for parent in path.parents:
        item = parent.lstat()
        if not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022:
            raise RuntimeFault("private_node_directory_untrusted")
    data = path.read_bytes()
    if len(data) > limit:
        raise RuntimeFault("private_node_file_oversized")
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # Never forward a scoped node credential to a redirect destination.
        return None


class Control:
    def __init__(self, configuration: Configuration, credential: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
            raise RuntimeFault("node_credential_invalid")
        self.configuration, self.credential = configuration, credential
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def post(self, operation: str, payload: dict[str, object]) -> dict[str, Any]:
        if operation not in {"inventory", "start", "poll"}:
            raise RuntimeFault("control_operation_forbidden")
        request = urllib.request.Request(  # noqa: S310 - validated HTTPS origin; redirects disabled
            self.configuration.api_origin + "/api/internal/v3/windows/" + operation,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Agent-Node-Id": str(self.configuration.node_id),
                "X-Agent-Credential": self.credential,
                "X-Agent-Timestamp": str(int(time.time())),
                "X-Agent-Nonce": str(uuid4()),
            },
        )
        try:
            with self.opener.open(request, timeout=5) as response:
                raw = response.read(131073)
        except (OSError, urllib.error.URLError):
            raise RuntimeFault("control_transport_unavailable") from None
        if len(raw) > 131072:
            raise RuntimeFault("control_response_oversized")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeFault("control_response_invalid")
        return result


def register(runtime: WindowRuntime, control: Control, public_keys: list[str]) -> None:
    observed = control.post("inventory", {})
    previous = observed.get("runtime_id")
    if previous == str(runtime.runtime_id):
        return  # Server confirms this process's epoch after a lost start response.
    if previous is not None:
        previous = str(UUID(str(previous)))
    result = control.post(
        "start",
        {
            "contract_version": 3,
            "runtime_id": str(runtime.runtime_id),
            "previous_runtime_id": previous,
            "capabilities": sorted(CAPABILITIES),
            "server_public_keys": public_keys,
            "gates_closed": True,
        },
    )
    if result.get("runtime_id") != str(runtime.runtime_id):
        raise RuntimeFault("registered_runtime_conflict")


def tick(runtime: WindowRuntime, control: Control) -> None:
    runtime.expire()
    active = []
    for slot, gate in runtime.gates.items():
        current = runtime.journal.current(slot)
        if current and current[1] == "active" and gate.is_live():
            active.append(str(current[0].window_id))
    payload: dict[str, object] = {
        "contract_version": 3,
        "runtime_id": str(runtime.runtime_id),
        "receipts": [receipt.model_dump(mode="json") for receipt in runtime.journal.pending()[:32]],
        "active_windows": active,
    }
    result = WindowCommands.model_validate(control.post("poll", payload))
    if result.runtime_id != runtime.runtime_id:
        raise RuntimeFault("response_runtime_conflict")
    for receipt in result.acknowledged_receipts:
        runtime.journal.acknowledge(receipt)
    for command in result.commands:
        if command.operation == "recover":
            if result.ready:
                raise RuntimeFault("unexpected_recovery_phase")
            runtime.recover(command.grant)
        elif command.operation == "revoke":
            runtime.close_grant(command.grant)
        elif command.operation == "window":
            if not result.ready:
                raise RuntimeFault("unexpected_admission_phase")
            try:
                runtime.apply(command.grant)
            except (ContractError, RuntimeFault):
                # Late/declined valid grants close; no blind reopening or guessed debit.
                runtime.close_grant(command.grant)


def stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    gates: dict[int, Gate] = {}
    peers: dict[int, Peer] = {}
    native_gates: list[SlotGate] = []
    native_peers: list[SlotPeer] = []
    runtime = None
    stage = "preflight"
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise RuntimeFault("linux_root_required")
        config = Configuration.model_validate_json(private_read(CONFIG, 4096))
        credential = private_read(CREDENTIAL, 256).decode("ascii").strip()
        public_keys = []
        for slot in range(1, config.slot_count + 1):
            ready = ReadySlot.model_validate_json(private_read(READY / f"{slot}.json", 4096))
            if ready.slot_id != slot:
                raise RuntimeFault("ready_slot_conflict")
            fd = os.open(f"/proc/{ready.native_pid}/ns/net", os.O_RDONLY)
            try:
                if os.fstat(fd).st_ino != ready.native_netns_inode:
                    raise RuntimeFault("ready_core_namespace_conflict")
                peer = SlotPeer(slot, AWG, ready.native_pid, fd, ready.server_public_key)
                native_peers.append(peer)
                gate = SlotGate(slot, fd)
                native_gates.append(gate)
                gates[slot], peers[slot] = gate, peer
                public_keys.append(ready.server_public_key)
            finally:
                os.close(fd)
        control = Control(config, credential)
        with open_private_journal(STATE) as connection:
            runtime = WindowRuntime(config.node_id, WindowJournal(connection), gates, peers)
            stage = "startup_close"
            runtime.startup()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            registered = False
            failed = False
            while not STOP:
                stage = "control_poll"
                try:
                    if not registered:
                        register(runtime, control, public_keys)
                        registered = True
                    tick(runtime, control)
                    if failed:
                        print('{"event":"control_recovered"}', flush=True)
                    failed = False
                except RuntimeFault:
                    if not failed:
                        print('{"event":"control_unavailable"}', flush=True)
                    failed = True
                time.sleep(1 if not failed else 3)
            stage = "shutdown_close"
            for slot in gates:
                current = runtime.journal.current(slot)
                if current is not None:
                    runtime.revoke(current[0].connection_id)
        return 0
    except Exception:
        print(json.dumps({"error": "node_agent_failed", "stage": stage}), flush=True)
        return 1
    finally:
        # Always attempt every gate, including when only part of startup completed.
        for gate in native_gates:
            try:
                gate.close()
            except Exception:
                print('{"event":"gate_close_failed"}', flush=True)
            gate.dispose()
        for peer in native_peers:
            peer.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

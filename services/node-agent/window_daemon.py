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
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import Field, ValidationError, field_validator
from window_gate import GateError, SlotGate
from window_peer import PeerFault, SlotPeer
from window_pool import PoolGate, PoolInterface, PoolPeer

from vpn_api.tunnels.v3_contracts import (
    CAPABILITIES,
    POOL_CAPABILITIES,
    ContractError,
    FrozenInput,
    WindowCommands,
)
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
    # ADR-101: when set, slot 1 is a pool hosting this many peers (and slot_count must be 1).
    pool_peers: int | None = Field(default=None, strict=True, ge=2, le=250)

    @field_validator("pool_peers")
    @classmethod
    def pool_single_slot(cls, value: int | None, info: Any) -> int | None:
        if value is not None and info.data.get("slot_count") != 1:
            raise ValueError("A pool node has exactly one operator slot")
        return value

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


REJECTION_BODY_LIMIT = 4096
REJECTION_CODE = re.compile(r"[a-z_]{1,64}")
RUNTIME_MISMATCH = "rejected_window_runtime_mismatch"


def rejection_code(answered: urllib.error.HTTPError) -> str:
    """ADR-170: `rejected_<server code>` from at most 4 KiB of body, else `control_rejected`."""
    try:
        body = answered.read(REJECTION_BODY_LIMIT + 1)
    except (OSError, ValueError):
        body = b""
    finally:
        answered.close()
    code = None
    if len(body) <= REJECTION_BODY_LIMIT:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        error = parsed.get("error") if isinstance(parsed, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str) and REJECTION_CODE.fullmatch(code):
        return "rejected_" + code
    return "control_rejected"


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
        except urllib.error.HTTPError as answered:
            # ADR-170: the server answered. Report its fixed error code, not "transport".
            raise RuntimeFault(rejection_code(answered)) from None
        except (OSError, urllib.error.URLError):
            raise RuntimeFault("control_transport_unavailable") from None
        if len(raw) > 131072:
            raise RuntimeFault("control_response_oversized")
        try:
            result = json.loads(raw)
        except ValueError:
            raise RuntimeFault("control_response_invalid") from None
        if not isinstance(result, dict):
            raise RuntimeFault("control_response_invalid")
        return result


def register(
    runtime: WindowRuntime,
    control: Control,
    public_keys: list[str],
    capabilities: frozenset[str] = CAPABILITIES,
) -> None:
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
            "capabilities": sorted(capabilities),
            "server_public_keys": public_keys,
            "gates_closed": True,
        },
    )
    if result.get("runtime_id") != str(runtime.runtime_id):
        raise RuntimeFault("registered_runtime_conflict")


# ADR-169: native and OS errors raised by gate/peer operations. One failed operation must not
# end the process, because every exit closes every gate on the node.
NATIVE_ERRORS: tuple[type[BaseException], ...] = (
    GateError,
    PeerFault,
    subprocess.SubprocessError,
    OSError,
    ValueError,
)
# Codes (and OS errors) meaning the operator core or its namespace is gone or replaced. Only
# these, repeated, stop the daemon: there is nothing left to serve and a restart is the cure.
IDENTITY_CODES = frozenset(
    {
        "operator_artifact_untrusted",
        "operator_core_identity_invalid",
        "operator_server_key_conflict",
        "operator_server_port_conflict",
        "host_namespace_forbidden",
    }
)
IDENTITY_FAILURE_LIMIT = 3
COMMAND_ERRORS: tuple[type[BaseException], ...] = (ContractError, RuntimeFault, *NATIVE_ERRORS)


def identity_failure(error: BaseException) -> bool:
    if isinstance(error, (GateError, PeerFault)):
        return str(error) in IDENTITY_CODES
    return isinstance(error, (FileNotFoundError, ProcessLookupError))


def report(event: str, error: BaseException, operation: str | None = None) -> None:
    # Fixed event and class name only; never the native message, a key or a grant.
    record = {"event": event, "error": type(error).__name__}
    if operation is not None:
        record["operation"] = operation
    print(json.dumps(record), flush=True)


def run_command(runtime: WindowRuntime, operation: str, grant: Any) -> None:
    """One command. Native failures are contained to this command (ADR-169)."""
    try:
        if operation == "recover":
            runtime.recover(grant)
        elif operation == "revoke":
            runtime.close_grant(grant)
        else:
            try:
                runtime.apply(grant)
            except COMMAND_ERRORS as declined:
                if identity_failure(declined):
                    raise
                if isinstance(declined, RuntimeFault) and str(declined) == "receipt_ack_required":
                    # ADR-183 (L5): an unacknowledged receipt on this slot is a backlog, not a
                    # refusal. Nothing was changed; the grant stays pending on the server and
                    # is applied once the receipt is acknowledged (a later poll).
                    report("window_command_deferred", declined, operation)
                    return
                # Late/declined/failed grants close; no blind reopening or guessed debit.
                runtime.close_grant(grant)
    except COMMAND_ERRORS as error:
        if identity_failure(error):
            raise
        # The server keeps the command due; it is retried on the next poll.
        report("window_command_failed", error, operation)


def tick(runtime: WindowRuntime, control: Control) -> None:
    try:
        runtime.expire()
    except NATIVE_ERRORS as error:
        if identity_failure(error):
            raise
        report("window_expire_failed", error)
    active = []
    try:
        for slot, gate in runtime.gates.items():
            current = runtime.journal.current(slot)
            if current and current[1] == "active" and gate.is_live():
                active.append(str(current[0].window_id))
    except NATIVE_ERRORS as error:
        if identity_failure(error):
            raise
        # A wrong active list would make the server revoke live peers; skip this poll.
        raise RuntimeFault("gate_state_unavailable") from None
    payload: dict[str, object] = {
        "contract_version": 3,
        "runtime_id": str(runtime.runtime_id),
        "receipts": [receipt.model_dump(mode="json") for receipt in runtime.journal.pending()[:32]],
        "active_windows": active,
    }
    try:
        result = WindowCommands.model_validate(control.post("poll", payload))
    except ValidationError:
        raise RuntimeFault("control_response_invalid") from None
    if result.runtime_id != runtime.runtime_id:
        raise RuntimeFault("response_runtime_conflict")
    if any(c.operation == "recover" for c in result.commands) and result.ready:
        raise RuntimeFault("unexpected_recovery_phase")
    if any(c.operation == "window" for c in result.commands) and not result.ready:
        raise RuntimeFault("unexpected_admission_phase")
    for receipt in result.acknowledged_receipts:
        runtime.journal.acknowledge(receipt)
    for command in result.commands:
        run_command(runtime, command.operation, command.grant)


def stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    gates: dict[int, Gate] = {}
    peers: dict[int, Peer] = {}
    native_gates: list[SlotGate] = []
    native_peers: list[SlotPeer] = []
    pools: list[PoolInterface] = []
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
                public_keys.append(ready.server_public_key)
                if config.pool_peers is not None:
                    pool = PoolInterface(AWG, ready.native_pid, fd, ready.server_public_key)
                    pools.append(pool)
                    for index in range(1, config.pool_peers + 1):
                        gates[index] = PoolGate(index, pool)
                        peers[index] = PoolPeer(index, pool)
                    continue
                peer = SlotPeer(slot, AWG, ready.native_pid, fd, ready.server_public_key)
                native_peers.append(peer)
                gate = SlotGate(slot, fd)
                native_gates.append(gate)
                gates[slot], peers[slot] = gate, peer
            finally:
                os.close(fd)
        control = Control(config, credential)
        capabilities = CAPABILITIES if config.pool_peers is None else POOL_CAPABILITIES
        with open_private_journal(STATE) as connection:
            runtime = WindowRuntime(
                config.node_id,
                WindowJournal(connection),
                gates,
                peers,
                meter="kernel-outer-ip.v1" if config.pool_peers is None else "kernel-inner-ip.v1",
            )
            stage = "startup_close"
            runtime.startup()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            registered = False
            failed = False
            identity_failures = 0
            while not STOP:
                stage = "control_poll"
                try:
                    if not registered:
                        register(runtime, control, public_keys, capabilities)
                        registered = True
                    tick(runtime, control)
                    if failed:
                        print('{"event":"control_recovered"}', flush=True)
                    failed = False
                    identity_failures = 0
                except RuntimeFault as fault:
                    if not failed:
                        print('{"event":"control_unavailable"}', flush=True)
                    failed = True
                    if str(fault) == RUNTIME_MISMATCH:
                        # ADR-170: the server no longer knows this runtime (for example after a
                        # database restore). Register again; start_epoch compares epochs.
                        print('{"event":"control_runtime_mismatch"}', flush=True)
                        registered = False
                except Exception as error:  # noqa: BLE001 - ADR-169 failure policy
                    # Exiting closes every gate on the node, so only a core or namespace that
                    # is gone, seen repeatedly, ends the process. Everything else is retried.
                    if identity_failure(error):
                        identity_failures += 1
                        report("operator_identity_failed", error)
                        if identity_failures >= IDENTITY_FAILURE_LIMIT:
                            stage = "operator_identity"
                            raise
                    else:
                        report("node_loop_error", error)
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
        for index, pool_gate in gates.items():
            if isinstance(pool_gate, PoolGate):
                try:
                    pool_gate.close()
                except Exception:
                    print(json.dumps({"event": "gate_close_failed", "peer": index}), flush=True)
        for pool in pools:
            pool.dispose()
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

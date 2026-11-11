"""Convert an installed beta node to pool mode in place (ADR-101): many devices at once.

The node keeps its name, id, keys, endpoint and credential, so users' saved server choices stay
valid. Read-only unless ``--execute`` is given. With ``--execute``:

1. drain: open connections on the node are revoked and the tool waits for their receipts;
2. the node row is set ``disabled`` (no new admissions) and the node agent is stopped;
3. on the node: config and journal are backed up, slot 1 is rebuilt as a pool (``pool_peers``),
   the journal starts fresh;
4. on the API host: the node's manifest entry gets ``pool_peers`` and ``latency_probe``;
5. the row is set ``active`` again, the agent starts, and the tool waits for its authenticated poll.

Any failure after step 3 restores the node files and the manifest from their backups and brings
the node back in its previous mode. Prerequisites: the node agent code is updated
(``update_beta_node_agent``) and the API runs migrations 0023-0025 (``deploy_beta_api_0025``).
Keys, credentials and addresses are never printed.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from scripts.beta_nodes import InventoryError, load_inventory
from scripts.connect_beta_node import (
    API_PROLOGUE,
    API_RESTORE_FILES,
    API_SET_STATUS,
    API_WAIT_RUNTIME,
    DEFAULT_INVENTORY,
    NODE_ENABLE_BOOT,
    NODE_START,
    NODE_STOP,
    ConnectError,
    Hosts,
    _node_id_from_agent,
    expect,
    parse_markers,
)

# Read-only. argv: node_id name region
API_POOL_STATUS = (
    API_PROLOGUE
    + r"""
from vpn_api.tunnels.window_profiles import WindowNode
from vpn_api.tunnels.window_service import runtime_ready
node_id, name, region = UUID(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = load_settings()
_raw, manifest, _info = read_manifest()
entry = next((n for n in manifest.nodes if n.node_id == node_id), None)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, peers = row_state(session, node_id, name, region)
        drained = runtime_ready(session, node_id, UUID(int=0))
finally: engine.dispose()
print("row=" + node.status)
print("open_peers=" + str(peers))
print("drained=" + str(int(drained)))
print("in_manifest=" + str(int(entry is not None)))
print("pool_peers=" + str(getattr(entry, "pool_peers", None) or 0))
print("api_supports_pool=" + str(int("pool_peers" in WindowNode.model_fields)))
"""
)

# argv: node_id name region. Revokes this node's open connections; the agent closes them.
API_DRAIN = (
    API_PROLOGUE
    + r"""
from sqlalchemy import update
node_id, name, region = UUID(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = load_settings()
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, _peers = row_state(session, node_id, name, region)
        result = session.execute(
            update(PeerConnection)
            .where(PeerConnection.node_id == node_id, PeerConnection.state.in_(("pending", "active")))
            .values(state="revoke_pending")
        )
finally: engine.dispose()
print("drain_requested=" + str(result.rowcount))
"""
)

# argv: node_id name region peers tag. Adds pool_peers + latency_probe to this node's entry.
API_SET_POOL = (
    API_PROLOGUE
    + r"""
from vpn_api.tunnels.window_profiles import WindowNode
from vpn_api.tunnels.window_service import runtime_ready
node_id, name, region, peers, tag = UUID(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]), tag_ok(sys.argv[5])
if not 2 <= peers <= 250: raise SystemExit(40)
if "pool_peers" not in WindowNode.model_fields: raise SystemExit(41)  # API code predates ADR-101
settings = load_settings()
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, open_peers = row_state(session, node_id, name, region)
        if node.status != "disabled" or open_peers or not runtime_ready(session, node_id, UUID(int=0)): raise SystemExit(42)
finally: engine.dispose()
old_raw, _manifest, info = read_manifest()
data = json.loads(old_raw)
entries = [n for n in data["nodes"] if n.get("node_id") == str(node_id)]
if len(entries) != 1 or len(entries[0].get("slots", [])) != 1: raise SystemExit(43)
entries[0]["pool_peers"] = peers
entries[0]["latency_probe"] = True
new_raw = canonical_json(data)
WindowManifest.model_validate_json(new_raw)
if new_raw == old_raw:
    print("manifest_already_pool")
else:
    backup = replace_file(MANIFEST, new_raw, info, tag, "pre-add")
    _raw, check, _info = read_manifest()
    if next(n for n in check.nodes if n.node_id == node_id).pool_peers != peers: raise SystemExit(44)
    print("manifest_pool_set backup=" + backup.name)
"""
)

# argv: peers tag. Runs on the node with the system python3; agent must already be stopped.
NODE_CONVERT = r"""
import json, os, shutil, sqlite3, stat, subprocess, sys, time
from pathlib import Path
peers, tag = int(sys.argv[1]), sys.argv[2]
if not 2 <= peers <= 250 or len(tag) != 32 or not all(c in "0123456789abcdef" for c in tag): raise SystemExit(40)
ROOT = Path("/etc/detourvpn-node"); STATE = Path("/var/lib/detourvpn-node"); BACKUP = Path("/root/detourvpn-pool-backup-" + tag)
def ctl(*args): return subprocess.run(["/usr/bin/systemctl", *args], capture_output=True, text=True, check=False)
def active(unit): return ctl("is-active", unit).stdout.strip()
def write(path, value):
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")
    temp = path.with_name("." + path.name + ".new-" + tag)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data); out.flush(); os.fsync(out.fileno())
    os.replace(temp, path)
if active("detourvpn-beta-node-agent.service") != "inactive": raise SystemExit(41)
if not Path("/opt/detourvpn-node/window_pool.py").is_file(): raise SystemExit(42)  # agent code not updated
slots = json.loads((ROOT / "slots.json").read_bytes()); agent = json.loads((ROOT / "agent.json").read_bytes())
if slots.get("pool_peers") == peers and agent.get("pool_peers") == peers:
    print("node_already_pool"); raise SystemExit(0)
if len(slots.get("slots", [])) != 1 or agent.get("slot_count") != 1: raise SystemExit(43)
journal = STATE / "state.sqlite"
if journal.exists():
    db = sqlite3.connect("file:" + str(journal) + "?mode=ro", uri=True)
    try:
        if db.execute("select count(*) from receipts where acknowledged=0").fetchone()[0]: raise SystemExit(44)
        if db.execute("select count(*) from slots where state != 'closed'").fetchone()[0]: raise SystemExit(45)
    finally: db.close()
BACKUP.mkdir(mode=0o700)
for name in ("slots.json", "agent.json"): shutil.copy2(ROOT / name, BACKUP / name)
moved = [p for p in (journal, STATE / "state.sqlite-wal", STATE / "state.sqlite-shm") if p.exists()]
def restore():
    ctl("stop", "detourvpn-beta-slot-bootstrap.service")
    for name in ("slots.json", "agent.json"): shutil.copy2(BACKUP / name, ROOT / name)
    for p in moved:
        if (BACKUP / p.name).exists() and not p.exists(): shutil.move(str(BACKUP / p.name), str(p))
    ctl("start", "detourvpn-beta-slot-bootstrap.service")
try:
    if ctl("stop", "detourvpn-beta-slot-bootstrap.service").returncode: raise RuntimeError
    if active("detourvpn-beta-slot-bootstrap.service") != "inactive": raise RuntimeError
    slots["pool_peers"] = peers; agent["pool_peers"] = peers
    write(ROOT / "slots.json", slots); write(ROOT / "agent.json", agent)
    for p in moved: shutil.move(str(p), str(BACKUP / p.name))
    if ctl("start", "detourvpn-beta-slot-bootstrap.service").returncode: raise RuntimeError
    for _ in range(30):
        if active("detourvpn-beta-core@1.service") == "active" and Path("/run/detourvpn-node/slots/1.json").is_file(): break
        time.sleep(1)
    else: raise RuntimeError
except BaseException:
    restore(); print("node_conversion_rolled_back"); raise SystemExit(50)
print("node_converted_to_pool")
"""

# argv: tag. Puts the pre-conversion files back (agent must be stopped).
NODE_RESTORE = r"""
import shutil, subprocess, sys
from pathlib import Path
tag = sys.argv[1]
if len(tag) != 32 or not all(c in "0123456789abcdef" for c in tag): raise SystemExit(40)
BACKUP = Path("/root/detourvpn-pool-backup-" + tag)
if not BACKUP.is_dir(): print("node_nothing_to_restore"); raise SystemExit(0)
subprocess.run(["/usr/bin/systemctl", "stop", "detourvpn-beta-node-agent.service"], check=False)
subprocess.run(["/usr/bin/systemctl", "stop", "detourvpn-beta-slot-bootstrap.service"], check=False)
for name in ("slots.json", "agent.json"): shutil.copy2(BACKUP / name, Path("/etc/detourvpn-node") / name)
state = Path("/var/lib/detourvpn-node")
for name in ("state.sqlite", "state.sqlite-wal", "state.sqlite-shm"):
    if (BACKUP / name).exists():
        (state / name).unlink(missing_ok=True)
        shutil.copy2(BACKUP / name, state / name)
if subprocess.run(["/usr/bin/systemctl", "start", "detourvpn-beta-slot-bootstrap.service"], check=False).returncode: raise SystemExit(41)
print("node_restored")
"""

# Read-only. Is the node's agent code new enough to run a pool?
NODE_POOL_READY = r"""
import json
from pathlib import Path
slots = json.loads(Path("/etc/detourvpn-node/slots.json").read_bytes())
print("agent_code_pool=" + str(int(Path("/opt/detourvpn-node/window_pool.py").is_file())))
print("node_pool_peers=" + str(slots.get("pool_peers") or 0))
print("node_slots=" + str(len(slots.get("slots", []))))
"""


class ConvertError(RuntimeError):
    """Fixed codes only."""


def status(hosts: Hosts, node_id: UUID) -> dict[str, str]:
    node = hosts.node
    values = parse_markers(
        hosts.api(API_POOL_STATUS, (str(node_id), node.name, node.region), label="pool_status")
    )
    values.update(parse_markers(hosts.node_run(NODE_POOL_READY, label="node_ready")))
    return values


def drain(hosts: Hosts, node_id: UUID, wait_s: int = 120) -> None:
    node = hosts.node
    args = (str(node_id), node.name, node.region)
    hosts.api(API_DRAIN, args, label="drain")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        values = parse_markers(hosts.api(API_POOL_STATUS, args, label="drain_status"))
        if values.get("open_peers") == "0" and values.get("drained") == "1":
            return
        time.sleep(3)
    raise ConvertError("convert_drain_timeout")


def convert(hosts: Hosts, peers: int, execute_changes: bool) -> dict[str, object]:
    node = hosts.node
    node_id = _node_id_from_agent(hosts)
    before = status(hosts, node_id)
    report: dict[str, object] = {"node_id_known": True, "peers": peers, "before": before}
    if before.get("agent_code_pool") != "1":
        raise ConvertError("convert_agent_code_not_updated")
    if before.get("api_supports_pool") != "1":
        raise ConvertError("convert_api_not_updated")
    if before.get("in_manifest") != "1" or before.get("node_slots") != "1":
        raise ConvertError("convert_node_shape_unsupported")
    if not execute_changes:
        return {"converted": False, **report}
    args = (str(node_id), node.name, node.region)
    for _attempt in range(3):
        drain(hosts, node_id)
        try:
            expect(
                hosts.api(API_SET_STATUS, (*args, "disabled"), label="disable"),
                "node_row_disabled",
                "convert_disable_unverified",
            )
            break
        except ConnectError:
            continue  # a new connection slipped in between drain and disable; drain again
    else:
        raise ConvertError("convert_disable_failed")
    tag = uuid.uuid4().hex
    node_changed = False
    try:
        expect(
            hosts.node_run(NODE_STOP, label="stop"),
            "beta_node_agent_stopped",
            "convert_stop_failed",
        )
        node_changed = True
        expect(
            hosts.node_run(NODE_CONVERT, (str(peers), tag), label="convert"),
            "node_",
            "convert_node_failed",
        )
        expect(
            hosts.api(API_SET_POOL, (*args, str(peers), tag), label="manifest"),
            "manifest_",
            "convert_manifest_failed",
        )
        expect(
            hosts.api(API_SET_STATUS, (*args, "active"), label="enable"),
            "node_row_active",
            "convert_enable_failed",
        )
        expect(
            hosts.node_run(NODE_START, label="start"),
            "beta_node_agent_active",
            "convert_start_failed",
        )
        expect(
            hosts.node_run(NODE_ENABLE_BOOT, label="boot"),
            "beta_node_agent_enabled",
            "convert_boot_failed",
        )
        expect(
            hosts.api(API_WAIT_RUNTIME, (str(node_id),), label="wait"),
            "beta_node_authenticated",
            "convert_runtime_not_seen",
        )
    except (ConnectError, ConvertError) as error:
        # Put everything back: manifest, node files and journal, then the previous mode.
        # Each step runs on its own so one failure cannot skip the rest.
        steps: list[Callable[[], bytes]] = [
            lambda: hosts.api(API_RESTORE_FILES, (tag,), label="restore_manifest"),
            lambda: (
                hosts.node_run(NODE_RESTORE, (tag,), label="restore_node") if node_changed else b""
            ),
            lambda: hosts.api(API_SET_STATUS, (*args, "active"), label="reenable"),
            lambda: hosts.node_run(NODE_START, label="restart"),
        ]
        failed = 0
        for step in steps:
            try:
                step()
            except ConnectError:
                failed += 1
        # Re-enabling fails harmlessly when the row was already active again.
        outcome = "rolled_back" if failed <= 1 else "rollback_incomplete"
        raise ConvertError(f"convert_failed_{outcome}:{error}") from None
    report["after"] = status(hosts, node_id)
    return {"converted": True, **report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--peers", type=int, default=32, help="simultaneous devices (2..250)")
    parser.add_argument(
        "--execute", action="store_true", help="really convert (default: read-only)"
    )
    arguments = parser.parse_args(argv)
    try:
        if not 2 <= arguments.peers <= 250:
            raise ConvertError("convert_peers_invalid")
        node = load_inventory(arguments.inventory).get(arguments.node)
        if node is None:
            raise ConvertError("convert_node_not_in_inventory")
        result = convert(Hosts(node), arguments.peers, arguments.execute)
    except (InventoryError, ConnectError, ConvertError) as error:
        print(json.dumps({"converted": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"node": node.name, "mode": "execute" if arguments.execute else "dry_run", **result},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

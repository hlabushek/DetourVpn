"""Connect an installed candidate node to the running beta control plane (ADR-094, stage 2b).

Subcommands, all dry-run (read-only) unless ``--execute`` is given:

* ``register``  create the node row as ``disabled`` and keep its one-time credential under /run
* ``bind``      stage agent config on the node, allow its IP in Caddy, add it to the window manifest
* ``activate``  set the row ``active``, start the node agent, wait for an authenticated poll
* ``rollback``  stop the agent and set the row ``disabled`` again (manifest and Caddy stay valid)

The API reads the window manifest on every request, so adding a node needs no API restart and the
running Helsinki node is never restarted. Every remote program is fixed, verifies the beta DB target
and prints only fixed markers; keys, credentials and addresses are never printed or logged.
"""

# E501 is disabled because the file embeds fixed remote Python programs.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import ipaddress
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from scripts.beta_nodes import BetaNode, InventoryError, load_inventory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.tunnels.window_profiles import WindowManifest  # noqa: E402

DEFAULT_INVENTORY = ROOT / "infra" / "beta" / "nodes.json"
API_HOST = "193.68.88.239"
API_ORIGIN = "https://193.68.88.239"
API_KEY = Path.home() / ".ssh" / "detourvpn-beta-20261026"
API_PYTHON = "/opt/detourvpn-beta/.venv/bin/python"
HELPER_SOURCE = ROOT / "scripts" / "provision_beta_node.py"
HELPER_REMOTE = "/opt/detourvpn-beta/scripts/provision_beta_node.py"
# Hash of the helper that the beta host runs today (deploy_beta_node_provisioning.ps1).
HELPER_PREVIOUS_SHA256 = "1563eceed748430fd86d815da2350efb3ffcbe9fe490a38a3569c82d75b9c0f3"
PROVISION_NAMESPACE = UUID("3b0f5f0e-6a43-4b8e-9d38-3d1f7f0a4f11")
NAME_PATTERN = re.compile(r"^detourvpn-beta-[a-z0-9]{2,24}$")
MAX_SLOTS = 16


class ConnectError(RuntimeError):
    """Fixed codes only; never include credentials, keys, addresses or raw remote output."""


def provision_key(node: BetaNode) -> UUID:
    """Deterministic idempotency key: repeating `register` can never create a second row."""
    return uuid5(PROVISION_NAMESPACE, node.name)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def merge_manifest(existing_raw: bytes, entry: dict[str, object]) -> bytes:
    """Add one node entry to the window manifest; re-adding an identical entry is a no-op."""
    manifest = json.loads(existing_raw)
    if manifest.get("version") != 3 or not isinstance(manifest.get("nodes"), list):
        raise ValueError("manifest_shape")
    for node in manifest["nodes"]:
        if node["node_id"] == entry["node_id"]:
            if node != entry:
                raise ValueError("manifest_conflict")
            return canonical_json(manifest)
    manifest["nodes"] = [*manifest["nodes"], entry]
    raw = canonical_json(manifest)
    if len(raw) > 65536:
        raise ValueError("manifest_size")
    WindowManifest.model_validate_json(raw)
    return raw


def render_caddyfile(text: str, new_ip: str) -> str:
    """Append one node IP to the single ``remote_ip`` matcher; fail closed on any surprise."""
    ipaddress.IPv4Address(new_ip)
    lines = text.split("\n")
    hits = [index for index, line in enumerate(lines) if line.strip().startswith("remote_ip ")]
    if len(hits) != 1:
        raise ValueError("caddy_remote_ip_not_unique")
    parts = lines[hits[0]].split()
    for value in parts[1:]:
        ipaddress.IPv4Address(value)
    if len(parts) < 2:
        raise ValueError("caddy_remote_ip_empty")
    if new_ip in parts[1:]:
        return text
    lines[hits[0]] = lines[hits[0]].rstrip() + " " + new_ip
    return "\n".join(lines)


def _embed(*functions: object) -> str:
    return "\n".join(inspect.getsource(function) for function in functions)  # type: ignore[arg-type]


API_PROLOGUE = r"""
import hashlib, ipaddress, json, os, re, shutil, stat, subprocess, sys, time, urllib.request
from pathlib import Path
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for _k in list(os.environ):
    if _k.startswith("VPN_"): os.environ.pop(_k, None)
from sqlalchemy import func, select
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine, transaction
from vpn_api.db.models import PeerConnection, VPNNode
from vpn_api.db.url import parse_local_database_url
from vpn_api.tunnels.window_profiles import WindowManifest
ENV = Path("/etc/detourvpn-beta/api.env")
MANIFEST = Path("/etc/detourvpn-beta/window-manifest.json")
CADDY = Path("/etc/caddy/Caddyfile")
def canonical_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
def load_settings():
    info = ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1: raise SystemExit(40)
    s = Settings(_env_file=ENV)
    u = parse_local_database_url(s.database_url)
    if not (s.environment == "closed" and s.enable_auth and s.enable_mini_app and s.beta_invites_only and s.enable_window_tunnels and s.window_manifest_file == MANIFEST and u.database == "detourvpn_beta" and u.username == "vpn_api" and u.host in {"localhost", "127.0.0.1"} and u.port == 25433): raise SystemExit(41)
    return s
def read_manifest():
    info = MANIFEST.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o640 or info.st_nlink != 1 or info.st_size > 65536: raise SystemExit(42)
    raw = MANIFEST.read_bytes()
    return raw, WindowManifest.model_validate_json(raw), info
def tag_ok(tag):
    if re.fullmatch(r"[0-9a-f]{32}", tag) is None: raise SystemExit(43)
    return tag
def replace_file(path, data, info, tag, suffix):
    backup = path.with_name(path.name + "." + suffix + "-" + tag)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(backup, flags, stat.S_IMODE(info.st_mode))
    with os.fdopen(fd, "wb") as out:
        out.write(path.read_bytes()); out.flush(); os.fsync(out.fileno())
    os.chown(backup, info.st_uid, info.st_gid)
    temp = path.with_name("." + path.name + ".new-" + tag)
    fd = os.open(temp, flags, stat.S_IMODE(info.st_mode))
    try:
        os.fchown(fd, info.st_uid, info.st_gid); os.fchmod(fd, stat.S_IMODE(info.st_mode))
        with os.fdopen(fd, "wb", closefd=True) as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try: os.fsync(dfd)
        finally: os.close(dfd)
    except BaseException:
        try: temp.unlink()
        except OSError: pass
        raise
    return backup
def row_state(session, node_id, name, region):
    node = session.scalar(select(VPNNode).where(VPNNode.id == node_id).with_for_update())
    if node is None or (node.name, node.region) != (name, region): raise SystemExit(44)
    open_peers = session.scalar(select(func.count()).select_from(PeerConnection).where(PeerConnection.node_id == node_id, PeerConnection.state != "closed"))
    return node, open_peers
"""

# Read-only. argv: name region key
API_STATUS = (
    API_PROLOGUE
    + r"""
name, region, key = sys.argv[1], sys.argv[2], sys.argv[3]
settings = load_settings()
_raw, manifest, _info = read_manifest()
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node = session.scalar(select(VPNNode).where(VPNNode.name == name))
        row = "absent" if node is None else ("mismatch" if node.region != region else node.status)
        in_manifest = int(node is not None and any(n.node_id == node.id for n in manifest.nodes))
        recent = int(node is not None and node.last_seen_at is not None and node.peer_runtime_id is not None)
finally: engine.dispose()
credential = Path("/run/detourvpn-beta") / ("node-credential-" + str(UUID(key)) + ".json")
print("row=" + row)
print("manifest_nodes=" + str(len(manifest.nodes)))
print("in_manifest=" + str(in_manifest))
print("agent_seen=" + str(recent))
print("credential=" + ("present" if credential.exists() else "absent"))
print("helper_sha=" + hashlib.sha256(Path("/opt/detourvpn-beta/scripts/provision_beta_node.py").read_bytes()).hexdigest())
print("caddy_ips=" + str(len([p for l in CADDY.read_text().splitlines() if l.strip().startswith("remote_ip ") for p in l.split()[1:]])))
"""
)

# argv: key name region. Prints the credential record (secret) to the local caller only.
API_READ_CREDENTIAL = (
    API_PROLOGUE
    + r"""
key, name, region = UUID(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = load_settings()
path = Path("/run/detourvpn-beta") / ("node-credential-" + str(key) + ".json")
directory = path.parent.lstat()
if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or stat.S_IMODE(directory.st_mode) != 0o700: raise SystemExit(45)
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096: raise SystemExit(46)
raw = path.read_bytes()
saved = json.loads(raw)
node_id = UUID(saved["node_id"])
if saved.get("version") != 1 or re.fullmatch(r"[A-Za-z0-9_-]{43}", saved["credential"]) is None: raise SystemExit(47)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, _peers = row_state(session, node_id, name, region)
        if node.status != "disabled": raise SystemExit(48)
finally: engine.dispose()
sys.stdout.buffer.write(raw)
"""
)

API_CLEANUP_CREDENTIAL = r"""
import hashlib, os, stat, sys
from pathlib import Path
from uuid import UUID
directory = Path("/run/detourvpn-beta")
info = directory.lstat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700: raise SystemExit(40)
path = directory / ("node-credential-" + str(UUID(sys.argv[1])) + ".json")
file_info = path.lstat()
if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != 0 or stat.S_IMODE(file_info.st_mode) != 0o600 or file_info.st_nlink != 1 or file_info.st_size > 4096: raise SystemExit(41)
if hashlib.sha256(path.read_bytes()).hexdigest() != sys.argv[2]: raise SystemExit(42)
path.unlink()
fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try: os.fsync(fd)
finally: os.close(fd)
print("credential_source_removed")
"""

# stdin: manifest entry JSON. argv: node_id name region host tag
API_MERGE_MANIFEST = (
    API_PROLOGUE
    + _embed(merge_manifest, canonical_json)
    + r"""
node_id, name, region, host, tag = UUID(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], tag_ok(sys.argv[5])
settings = load_settings()
raw_entry = sys.stdin.buffer.read(16385)
if len(raw_entry) > 16384: raise SystemExit(49)
entry = json.loads(raw_entry)
if entry.get("node_id") != str(node_id) or not 1 <= len(entry.get("slots", [])) <= 16: raise SystemExit(50)
for slot in entry["slots"]:
    slot_host, _sep, port = slot["endpoint"].rpartition(":")
    if slot_host != host or port != str(51900 + slot["slot_id"]): raise SystemExit(51)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, _peers = row_state(session, node_id, name, region)
        if node.status != "disabled": raise SystemExit(48)
finally: engine.dispose()
old_raw, _manifest, info = read_manifest()
new_raw = merge_manifest(old_raw, entry)
if new_raw == old_raw:
    print("manifest_already_contains_node")
else:
    backup = replace_file(MANIFEST, new_raw, info, tag, "pre-add")
    _raw, check, _info = read_manifest()
    if not any(n.node_id == node_id for n in check.nodes): raise SystemExit(52)
    print("manifest_merged backup=" + backup.name)
"""
)

# argv: node_ip tag
API_CADDY_ALLOW = (
    API_PROLOGUE
    + _embed(render_caddyfile)
    + r"""
node_ip, tag = sys.argv[1], tag_ok(sys.argv[2])
info = CADDY.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > 65536: raise SystemExit(40)
old = CADDY.read_text()
new = render_caddyfile(old, node_ip)
if new == old:
    print("caddy_already_allows_node"); raise SystemExit(0)
caddy = shutil.which("caddy") or "/usr/bin/caddy"
candidate = CADDY.with_name("Caddyfile.candidate-" + tag)
candidate.write_text(new); os.chmod(candidate, stat.S_IMODE(info.st_mode))
try:
    check = subprocess.run([caddy, "validate", "--config", str(candidate), "--adapter", "caddyfile"], capture_output=True, check=False)
finally:
    candidate.unlink()
if check.returncode: raise SystemExit(41)
backup = replace_file(CADDY, new.encode(), info, tag, "pre-add")
def restore():
    CADDY.write_bytes(backup.read_bytes())
    subprocess.run(["/usr/bin/systemctl", "reload", "caddy"], capture_output=True, check=False)
reload = subprocess.run(["/usr/bin/systemctl", "reload", "caddy"], capture_output=True, check=False)
ok = reload.returncode == 0 and subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", "caddy"], check=False).returncode == 0
if ok:
    probe = subprocess.run(["/usr/bin/curl", "-ks", "-m", "8", "-o", "/dev/null", "-w", "%{http_code}", "https://193.68.88.239/mini-app/"], capture_output=True, check=False)
    ok = probe.stdout.strip() == b"200"
if not ok:
    restore(); raise SystemExit(42)
print("caddy_allows_node backup=" + backup.name)
"""
)

# argv: tag
API_RESTORE_FILES = (
    API_PROLOGUE
    + r"""
tag = tag_ok(sys.argv[1])
for path in (MANIFEST, CADDY):
    backup = path.with_name(path.name + ".pre-add-" + tag)
    if backup.exists():
        info = backup.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1: raise SystemExit(40)
        target = path.lstat()
        replace_file(path, backup.read_bytes(), target, tag, "pre-restore")
subprocess.run(["/usr/bin/systemctl", "reload", "caddy"], capture_output=True, check=False)
print("pre_add_files_restored")
"""
)

# argv: node_id name region (verifies a bound-but-disabled node before activation)
API_ACTIVATE_PREFLIGHT = (
    API_PROLOGUE
    + r"""
node_id, name, region = UUID(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = load_settings()
_raw, manifest, _info = read_manifest()
if not any(n.node_id == node_id for n in manifest.nodes): raise SystemExit(45)
if not any(l.strip().startswith("remote_ip ") for l in CADDY.read_text().splitlines()): raise SystemExit(46)
if urllib.request.urlopen("http://127.0.0.1:28000/api/v1/ready", timeout=4).status != 200: raise SystemExit(47)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, peers = row_state(session, node_id, name, region)
        if node.status != "disabled" or peers: raise SystemExit(48)
finally: engine.dispose()
print("connect_activation_preflight_passed")
"""
)

# argv: node_id name region target(active|disabled)
API_SET_STATUS = (
    API_PROLOGUE
    + r"""
node_id, name, region, target = UUID(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
if target not in {"active", "disabled"}: raise SystemExit(40)
settings = load_settings()
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node, peers = row_state(session, node_id, name, region)
        if target == "active" and (node.status != "disabled" or peers): raise SystemExit(48)
        if target == "disabled" and peers: raise SystemExit(49)
        node.status = target
finally: engine.dispose()
print("node_row_" + target)
"""
)

# argv: node_id
API_WAIT_RUNTIME = (
    API_PROLOGUE
    + r"""
node_id = UUID(sys.argv[1])
settings = load_settings()
engine = create_database_engine(settings)
try:
    for _ in range(30):
        with transaction(engine) as session:
            node = session.scalar(select(VPNNode).where(VPNNode.id == node_id))
            if node is not None and node.status == "active" and node.peer_runtime_id is not None and node.last_seen_at is not None:
                print("beta_node_authenticated_inventory_and_poll"); break
        time.sleep(1)
    else: raise SystemExit(40)
finally: engine.dispose()
"""
)

NODE_READ_SLOTS = r"""
import json, stat, sys
from pathlib import Path
p = Path("/etc/detourvpn-node/slots.json")
info = p.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 65536: raise SystemExit(41)
data = json.loads(p.read_bytes())
if data.get("version") != 1 or not isinstance(data.get("slots"), list): raise SystemExit(42)
pool = data.get("pool_peers")
if pool is not None and (type(pool) is not int or not 2 <= pool <= 250 or len(data["slots"]) != 1): raise SystemExit(43)
out = {"version": 1, "slots": [{k: s[k] for k in ("slot_id", "endpoint", "server_public_key", "header_protection_key")} for s in data["slots"]]}
if pool is not None: out["pool_peers"] = pool
out["latency_probe"] = Path("/etc/systemd/system/detourvpn-beta-latency-probe.service").is_file()
sys.stdout.write(json.dumps(out, separators=(",", ":")))
"""

# stdin: {"node_id", "credential", "api_origin", "slot_count"}
NODE_STAGE = r"""
import json, os, re, stat, sys
from pathlib import Path
from uuid import UUID
payload = json.loads(sys.stdin.buffer.read(8193))
node_id = UUID(payload["node_id"]); credential = payload["credential"]; origin = payload["api_origin"]
if set(payload) != {"node_id", "credential", "api_origin", "slot_count"} or origin != sys.argv[1] or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None: raise SystemExit(41)
root = Path("/etc/detourvpn-node")
ri = root.lstat()
if not stat.S_ISDIR(ri.st_mode) or ri.st_uid != 0 or ri.st_mode & 0o077: raise SystemExit(42)
si = (root / "slots.json").lstat()
if not stat.S_ISREG(si.st_mode) or si.st_uid != 0 or si.st_mode & 0o077 or si.st_nlink != 1: raise SystemExit(43)
slots = json.loads((root / "slots.json").read_bytes())
if payload["slot_count"] != len(slots.get("slots", [])) or not 1 <= payload["slot_count"] <= 16: raise SystemExit(44)
agent = {"node_id": str(node_id), "api_origin": origin, "slot_count": payload["slot_count"]}
pool = slots.get("pool_peers")
if pool is not None:
    if type(pool) is not int or not 2 <= pool <= 250 or payload["slot_count"] != 1: raise SystemExit(44)
    agent["pool_peers"] = pool  # ADR-101: the installer's slots.json is the source of truth.
files = ((root / "agent.json", (json.dumps(agent, sort_keys=True, indent=2) + "\n").encode("ascii")), (root / "credential", (credential + "\n").encode("ascii")))
created = []
try:
    for path, data in files:
        try: info = path.lstat()
        except FileNotFoundError: info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1 or path.read_bytes() != data: raise SystemExit(45)
            continue
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created.append(path)
        with os.fdopen(fd, "wb") as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.chmod(path, 0o600, follow_symlinks=False)
except BaseException:
    for path in created:
        try: path.unlink()
        except OSError: pass
    raise
print("node_configuration_staged")
"""

NODE_UNSTAGE = r"""
import subprocess
from pathlib import Path
state = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-node-agent.service"], capture_output=True, text=True, check=False)
if state.stdout.strip() != "inactive": raise SystemExit(40)
for name in ("agent.json", "credential"):
    try: (Path("/etc/detourvpn-node") / name).unlink()
    except FileNotFoundError: pass
print("node_configuration_unstaged")
"""

# argv: node_id api_origin slot_count
NODE_PREFLIGHT = r"""
import json, stat, subprocess, sys
from pathlib import Path
from uuid import UUID
root = Path("/etc/detourvpn-node")
for name in ("agent.json", "credential", "slots.json"):
    i = (root / name).lstat()
    if not stat.S_ISREG(i.st_mode) or i.st_uid != 0 or stat.S_IMODE(i.st_mode) != 0o600 or i.st_nlink != 1: raise SystemExit(40)
config = json.loads((root / "agent.json").read_bytes())
if UUID(config["node_id"]) != UUID(sys.argv[1]) or config["api_origin"] != sys.argv[2] or config["slot_count"] != int(sys.argv[3]): raise SystemExit(41)
units = ["detourvpn-beta-slot-bootstrap.service"] + ["detourvpn-beta-core@%d.service" % n for n in range(1, int(sys.argv[3]) + 1)]
for unit in units:
    s = subprocess.run(["/usr/bin/systemctl", "is-active", unit], capture_output=True, text=True, check=False)
    if s.returncode != 0 or s.stdout.strip() != "active": raise SystemExit(42)
a = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-node-agent.service"], capture_output=True, text=True, check=False)
if a.stdout.strip() != "inactive": raise SystemExit(43)
print("beta_node_activation_preflight_passed")
"""

NODE_START = r"""
import subprocess, time
state = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-node-agent.service"], capture_output=True, text=True, check=False)
if state.stdout.strip() != "inactive": raise SystemExit(39)
if subprocess.run(["/usr/bin/systemctl", "start", "detourvpn-beta-node-agent.service"], capture_output=True, check=False).returncode: raise SystemExit(40)
for _ in range(15):
    s = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-node-agent.service"], capture_output=True, text=True, check=False)
    if s.returncode == 0 and s.stdout.strip() == "active":
        print("beta_node_agent_active"); raise SystemExit(0)
    time.sleep(1)
raise SystemExit(41)
"""

NODE_ENABLE_BOOT = r"""
import subprocess
if subprocess.run(["/usr/bin/systemctl", "enable", "detourvpn-beta-node-agent.service"], capture_output=True, check=False).returncode: raise SystemExit(40)
s = subprocess.run(["/usr/bin/systemctl", "is-enabled", "detourvpn-beta-node-agent.service"], capture_output=True, text=True, check=False)
if s.stdout.strip() != "enabled": raise SystemExit(41)
print("beta_node_agent_enabled_at_boot")
"""

NODE_STOP = r"""
import subprocess, time
if subprocess.run(["/usr/bin/systemctl", "stop", "detourvpn-beta-node-agent.service"], capture_output=True, check=False).returncode: raise SystemExit(40)
for _ in range(10):
    s = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-node-agent.service"], capture_output=True, text=True, check=False)
    if s.stdout.strip() == "inactive":
        print("beta_node_agent_stopped"); raise SystemExit(0)
    time.sleep(1)
raise SystemExit(41)
"""

NODE_PREFLIGHT_BOUND = r"""
import json, stat, sys
from pathlib import Path
from uuid import UUID
root = Path("/etc/detourvpn-node")
for name in ("agent.json", "credential"):
    i = (root / name).lstat()
    if not stat.S_ISREG(i.st_mode) or i.st_uid != 0 or stat.S_IMODE(i.st_mode) != 0o600 or i.st_nlink != 1: raise SystemExit(41)
config = json.loads((root / "agent.json").read_bytes())
if UUID(config["node_id"]) != UUID(sys.argv[1]) or config["api_origin"] != sys.argv[2] or config["slot_count"] != int(sys.argv[3]): raise SystemExit(42)
print("node_bound_files_verified")
"""

NODE_READ_AGENT_ID = r"""
import json, stat
from pathlib import Path
p = Path("/etc/detourvpn-node/agent.json")
i = p.lstat()
if not stat.S_ISREG(i.st_mode) or i.st_uid != 0 or stat.S_IMODE(i.st_mode) != 0o600 or i.st_nlink != 1: raise SystemExit(40)
print("node_id=" + json.loads(p.read_bytes())["node_id"])
"""

# Every fixed remote program, so tests can syntax-check them all.
PROGRAMS = {
    "API_PROLOGUE": API_PROLOGUE,
    "API_STATUS": API_STATUS,
    "API_READ_CREDENTIAL": API_READ_CREDENTIAL,
    "API_CLEANUP_CREDENTIAL": API_CLEANUP_CREDENTIAL,
    "API_MERGE_MANIFEST": API_MERGE_MANIFEST,
    "API_CADDY_ALLOW": API_CADDY_ALLOW,
    "API_RESTORE_FILES": API_RESTORE_FILES,
    "API_ACTIVATE_PREFLIGHT": API_ACTIVATE_PREFLIGHT,
    "API_SET_STATUS": API_SET_STATUS,
    "API_WAIT_RUNTIME": API_WAIT_RUNTIME,
    "NODE_READ_SLOTS": NODE_READ_SLOTS,
    "NODE_STAGE": NODE_STAGE,
    "NODE_UNSTAGE": NODE_UNSTAGE,
    "NODE_PREFLIGHT": NODE_PREFLIGHT,
    "NODE_PREFLIGHT_BOUND": NODE_PREFLIGHT_BOUND,
    "NODE_READ_AGENT_ID": NODE_READ_AGENT_ID,
    "NODE_START": NODE_START,
    "NODE_ENABLE_BOOT": NODE_ENABLE_BOOT,
    "NODE_STOP": NODE_STOP,
}


def python_command(interpreter: str, source: str, arguments: tuple[str, ...] = ()) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    code = f"import base64;exec(base64.b64decode('{encoded}'))"
    return " ".join(
        [shlex.quote(interpreter), "-c", shlex.quote(code), *map(shlex.quote, arguments)]
    )


def _ssh(
    host: str, key: Path, port: int, command: str, payload: bytes | None, timeout: int = 120
) -> bytes:
    if not key.is_file():
        raise ConnectError("connect_ssh_key_missing")
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(key), "-p", str(port), f"root@{host}", command,
    ]  # fmt: skip
    try:
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no local shell; fixed remote programs.
            argv, input=payload, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ConnectError("connect_ssh_unavailable") from None
    if result.returncode != 0:
        raise ConnectError(f"connect_remote_step_failed:{result.returncode}")
    return result.stdout


class Hosts:
    """Everything that talks to the two hosts, replaceable in tests."""

    def __init__(self, node: BetaNode) -> None:
        self.node = node

    def api(
        self,
        program: str,
        args: tuple[str, ...] = (),
        payload: bytes | None = None,
        label: str = "api",
    ) -> bytes:
        try:
            return _ssh(API_HOST, API_KEY, 22, python_command(API_PYTHON, program, args), payload)
        except ConnectError as error:
            raise ConnectError(f"{error}:{label}") from None

    def node_run(
        self,
        program: str,
        args: tuple[str, ...] = (),
        payload: bytes | None = None,
        label: str = "node",
    ) -> bytes:
        try:
            return _ssh(
                self.node.host,
                self.node.ssh_key,
                self.node.ssh_port,
                python_command("/usr/bin/python3", program, args),
                payload,
            )
        except ConnectError as error:
            raise ConnectError(f"{error}:{label}") from None

    def api_shell(
        self, command: str, payload: bytes | None = None, label: str = "api_shell"
    ) -> bytes:
        try:
            return _ssh(API_HOST, API_KEY, 22, command, payload)
        except ConnectError as error:
            raise ConnectError(f"{error}:{label}") from None


def parse_markers(output: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.decode("utf-8", "replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and re.fullmatch(r"[a-z_]{1,24}", key) and len(value) <= 80:
            values[key] = value
    return values


def api_status(hosts: Hosts) -> dict[str, str]:
    node = hosts.node
    return parse_markers(
        hosts.api(API_STATUS, (node.name, node.region, str(provision_key(node))), label="status")
    )


def expect(output: bytes, marker: str, code: str) -> None:
    if not output.decode("utf-8", "replace").strip().startswith(marker):
        raise ConnectError(code)


def deploy_helper(hosts: Hosts, current_sha: str) -> str:
    """Replace the host's provisioning helper atomically (with backup) if it is the known old one."""
    source = HELPER_SOURCE.read_bytes()
    local_sha = hashlib.sha256(source).hexdigest()
    if current_sha == local_sha:
        return "helper_current"
    if current_sha != HELPER_PREVIOUS_SHA256:
        raise ConnectError("connect_helper_unexpected_version")
    run = uuid4().hex
    stage = f"/tmp/detourvpn-helper-{run}.py"  # noqa: S108 - fixed name, removed below.
    hosts.api_shell(f"umask 077 && cat > {stage}", payload=source, label="helper_upload")
    script = (
        "set -Eeuo pipefail; h=/opt/detourvpn-beta/scripts/provision_beta_node.py; "
        f"test \"$(sha256sum {stage} | cut -d' ' -f1)\" = {local_sha}; "
        f"test \"$(sha256sum $h | cut -d' ' -f1)\" = {HELPER_PREVIOUS_SHA256}; "
        f"cp -a -- $h $h.pre-nodes-{run}; install -o root -g root -m 0600 {stage} $h.new-{run}; mv -- $h.new-{run} $h; "
        f"rm -f -- {stage}; "
        f"if ! /opt/detourvpn-beta/.venv/bin/python $h --help >/dev/null; then cp -a -- $h.pre-nodes-{run} $h; echo helper_rolled_back; exit 42; fi; "
        "echo helper_updated"
    )
    expect(
        hosts.api_shell("bash -c " + shlex.quote(script), label="helper_install"),
        "helper_updated",
        "connect_helper_update_failed",
    )
    return "helper_updated"


def register(hosts: Hosts, execute_changes: bool) -> dict[str, object]:
    node = hosts.node
    state = api_status(hosts)
    plan: dict[str, object] = {
        "row": state.get("row"),
        "helper_sha": state.get("helper_sha", "")[:12],
    }
    if state.get("row") != "absent":
        raise ConnectError("connect_register_row_exists")
    if not execute_changes:
        return {"registered": False, **plan}
    plan["helper"] = deploy_helper(hosts, state.get("helper_sha", ""))
    command = (
        f"{API_PYTHON} {HELPER_REMOTE} --idempotency-key {provision_key(node)} --name {shlex.quote(node.name)} "
        f"--region {shlex.quote(node.region)} --country-code {shlex.quote(node.country_code)} --city {shlex.quote(node.city)}"
    )
    output = hosts.api_shell(command, label="provision")
    try:
        created = json.loads(output)
        node_id = str(UUID(created["node_id"]))
        verified = created["created"] is True and created["status"] == "disabled"
    except (ValueError, KeyError, TypeError):
        raise ConnectError("connect_register_unverified") from None
    if not verified:
        raise ConnectError("connect_register_unverified")
    return {"registered": True, "node_id": node_id, "status": "disabled", **plan}


def inventory_of(hosts: Hosts) -> tuple[list[dict[str, object]], int | None, bool]:
    """The node's slots, its ADR-101 pool size (if any) and whether it runs the ADR-102 probe."""
    try:
        data = json.loads(hosts.node_run(NODE_READ_SLOTS, label="read_slots"))
        slots: list[dict[str, object]] = data["slots"]
        pool = data.get("pool_peers")
        probe = data.get("latency_probe", False) is True
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ConnectError("connect_slots_invalid") from None
    if not 1 <= len(slots) <= MAX_SLOTS:
        raise ConnectError("connect_slots_invalid")
    if pool is not None and (type(pool) is not int or not 2 <= pool <= 250 or len(slots) != 1):
        raise ConnectError("connect_slots_invalid")
    return slots, pool, probe


def slots_of(hosts: Hosts) -> list[dict[str, object]]:
    return inventory_of(hosts)[0]


def manifest_entry(
    node_id: UUID,
    slots: list[dict[str, object]],
    host: str,
    pool_peers: int | None = None,
    latency_probe: bool = False,
) -> dict[str, object]:
    rendered = []
    for expected, slot in enumerate(slots, start=1):
        endpoint = str(slot["endpoint"])
        if slot["slot_id"] != expected or endpoint != f"{host}:{51900 + expected}":
            raise ConnectError("connect_slot_endpoint_invalid")
        rendered.append(
            {
                key: slot[key]
                for key in ("slot_id", "endpoint", "server_public_key", "header_protection_key")
            }
        )
    entry: dict[str, object] = {"node_id": str(node_id), "slots": rendered}
    if pool_peers is not None:
        if len(rendered) != 1:
            raise ConnectError("connect_slot_endpoint_invalid")
        entry["pool_peers"] = pool_peers
    if latency_probe:
        entry["latency_probe"] = True
    return entry


def bind(hosts: Hosts, execute_changes: bool) -> dict[str, object]:
    node = hosts.node
    key = str(provision_key(node))
    state = api_status(hosts)
    if state.get("row") != "disabled" or state.get("credential") != "present":
        raise ConnectError("connect_bind_requires_registered_disabled_node")
    slots, pool_peers, latency_probe = inventory_of(hosts)
    summary: dict[str, object] = {
        "slots": len(slots),
        "pool_peers": pool_peers,
        "latency_probe": latency_probe,
        "in_manifest": state.get("in_manifest") == "1",
    }
    if not execute_changes:
        return {"bound": False, **summary}
    tag = uuid4().hex
    credential_raw = hosts.api(
        API_READ_CREDENTIAL, (key, node.name, node.region), label="read_credential"
    )
    try:
        record = json.loads(credential_raw)
        node_id = UUID(record["node_id"])
        credential = str(record["credential"])
    except (ValueError, KeyError, TypeError):
        raise ConnectError("connect_credential_invalid") from None
    entry = manifest_entry(node_id, slots, node.host, pool_peers, latency_probe)
    payload = canonical_json(
        {
            "node_id": str(node_id),
            "api_origin": API_ORIGIN,
            "slot_count": len(slots),
            "credential": credential,
        }
    )
    node_staged = False
    api_changed = False
    try:
        expect(
            hosts.node_run(NODE_STAGE, (API_ORIGIN,), payload, "stage_node"),
            "node_configuration_staged",
            "connect_node_stage_unverified",
        )
        node_staged = True
        api_changed = True
        expect(
            hosts.api(API_CADDY_ALLOW, (node.host, tag), label="caddy"),
            "caddy_",
            "connect_caddy_unverified",
        )
        expect(
            hosts.api(
                API_MERGE_MANIFEST,
                (str(node_id), node.name, node.region, node.host, tag),
                canonical_json(entry),
                "manifest",
            ),
            "manifest_",
            "connect_manifest_unverified",
        )
        expect(
            hosts.node_run(
                NODE_PREFLIGHT_BOUND,
                (str(node_id), API_ORIGIN, str(len(slots))),
                label="verify_node",
            ),
            "node_bound_files_verified",
            "connect_node_verify_failed",
        )
        digest = hashlib.sha256(credential_raw).hexdigest()
        expect(
            hosts.api(API_CLEANUP_CREDENTIAL, (key, digest), label="cleanup"),
            "credential_source_removed",
            "connect_cleanup_unverified",
        )
    except Exception as error:
        try:
            if api_changed:
                hosts.api(API_RESTORE_FILES, (tag,), label="restore")
            if node_staged:
                hosts.node_run(NODE_UNSTAGE, label="unstage")
        except ConnectError:
            raise ConnectError("connect_bind_failed_rollback_incomplete") from None
        raise ConnectError(f"connect_bind_failed_rolled_back:{error}") from None
    return {
        "bound": True,
        "node_id": str(node_id),
        "status": "disabled",
        "credential_source": "removed",
        "rollback_tag": tag,
        **summary,
    }


def activate(hosts: Hosts, execute_changes: bool) -> dict[str, object]:
    node = hosts.node
    state = api_status(hosts)
    if (
        state.get("row") != "disabled"
        or state.get("in_manifest") != "1"
        or state.get("credential") != "absent"
    ):
        raise ConnectError("connect_activate_requires_bound_disabled_node")
    slots = slots_of(hosts)
    node_id = _node_id_from_agent(hosts)
    args = (str(node_id), API_ORIGIN, str(len(slots)))
    expect(
        hosts.node_run(NODE_PREFLIGHT, args, label="node_preflight"),
        "beta_node_activation_preflight_passed",
        "connect_node_preflight_failed",
    )
    expect(
        hosts.api(
            API_ACTIVATE_PREFLIGHT, (str(node_id), node.name, node.region), label="api_preflight"
        ),
        "connect_activation_preflight_passed",
        "connect_api_preflight_failed",
    )
    if not execute_changes:
        return {"activated": False, "preflight": "passed"}
    try:
        expect(
            hosts.api(
                API_SET_STATUS, (str(node_id), node.name, node.region, "active"), label="row_active"
            ),
            "node_row_active",
            "connect_row_enable_failed",
        )
        expect(
            hosts.node_run(NODE_START, label="agent_start"),
            "beta_node_agent_active",
            "connect_agent_start_failed",
        )
        expect(
            hosts.api(API_WAIT_RUNTIME, (str(node_id),), label="wait_runtime"),
            "beta_node_authenticated_inventory_and_poll",
            "connect_agent_not_authenticated",
        )
        expect(
            hosts.node_run(NODE_ENABLE_BOOT, label="enable_boot"),
            "beta_node_agent_enabled_at_boot",
            "connect_enable_boot_failed",
        )
    except Exception as error:
        try:
            hosts.node_run(NODE_STOP, label="rollback_stop")
            hosts.api(
                API_SET_STATUS,
                (str(node_id), node.name, node.region, "disabled"),
                label="rollback_row",
            )
        except ConnectError:
            raise ConnectError("connect_activate_failed_rollback_incomplete") from None
        raise ConnectError(f"connect_activate_failed_rolled_back:{error}") from None
    return {
        "activated": True,
        "node_id": str(node_id),
        "status": "active",
        "node_agent": "active",
        "enabled_at_boot": True,
    }


def rollback(hosts: Hosts, execute_changes: bool) -> dict[str, object]:
    node = hosts.node
    node_id = _node_id_from_agent(hosts)
    if not execute_changes:
        return {"rolled_back": False, "node_id_known": True}
    expect(
        hosts.node_run(NODE_STOP, label="stop"),
        "beta_node_agent_stopped",
        "connect_rollback_stop_failed",
    )
    expect(
        hosts.api(API_SET_STATUS, (str(node_id), node.name, node.region, "disabled"), label="row"),
        "node_row_disabled",
        "connect_rollback_row_failed",
    )
    return {
        "rolled_back": True,
        "node_id": str(node_id),
        "status": "disabled",
        "node_agent": "inactive",
    }


def _node_id_from_agent(hosts: Hosts) -> UUID:
    try:
        return UUID(parse_markers(hosts.node_run(NODE_READ_AGENT_ID, label="agent_id"))["node_id"])
    except (KeyError, ValueError):
        raise ConnectError("connect_agent_config_invalid") from None


COMMANDS = {"register": register, "bind": bind, "activate": activate, "rollback": rollback}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[*COMMANDS, "status"])
    parser.add_argument("--node", required=True)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument(
        "--execute", action="store_true", help="really change hosts (default: read-only)"
    )
    parser.add_argument("--receipt", type=Path, help="write the sanitized result to a NEW file")
    arguments = parser.parse_args(argv)
    try:
        node = load_inventory(arguments.inventory).get(arguments.node)
        if node is None or NAME_PATTERN.fullmatch(node.name) is None:
            raise ConnectError("connect_node_not_in_inventory")
        if node.name == "detourvpn-beta-helsinki":
            raise ConnectError("connect_existing_node_refused")
        hosts = Hosts(node)
        if arguments.command == "status":
            result: dict[str, object] = dict(api_status(hosts))
            result.pop("helper_sha", None)
        else:
            result = COMMANDS[arguments.command](hosts, arguments.execute)
    except (InventoryError, ConnectError) as error:
        print(
            json.dumps({"ok": False, "command": arguments.command, "error": str(error)}),
            file=sys.stderr,
        )
        return 1
    report = {
        "node": node.name,
        "command": arguments.command,
        "mode": "execute" if arguments.execute else "dry_run",
        **result,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.receipt is not None:
        with arguments.receipt.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

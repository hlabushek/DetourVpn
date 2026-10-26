"""Enable the isolated beta node with guarded staged rollback."""

# E501 is disabled because this file embeds fixed remote Python programs.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

API_HOST = "193.68.88.239"
NODE_HOST = "95.81.73.183"
SSH_KEY = Path.home() / ".ssh" / "detourvpn-beta-20261026"
SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=yes",
    "-i",
    str(SSH_KEY),
)

NODE_ID = UUID("1d230f49-ed36-45f2-91ea-86fe212346f8")
MANIFEST = "/etc/detourvpn-beta/window-manifest.json"


class ActivationError(RuntimeError):
    """Fixed operator-facing code without remote diagnostics or secrets."""


def _python_command(interpreter: str, source: str, arguments: tuple[str, ...] = ()) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    code = f"import base64;exec(base64.b64decode('{encoded}'))"
    parts = [shlex.quote(interpreter), "-c", shlex.quote(code)]
    parts.extend(shlex.quote(value) for value in arguments)
    return " ".join(parts)


def _ssh(host: str, command: str) -> bytes:
    executable = shutil.which("ssh.exe") or shutil.which("ssh")
    if executable is None or not SSH_KEY.is_file():
        raise ActivationError("beta_activation_ssh_unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - fixed SSH hosts and quoted fixed remote programs
            [executable, *SSH_OPTIONS, f"root@{host}", command],
            capture_output=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ActivationError("beta_activation_ssh_failed") from None
    if result.returncode:
        raise ActivationError("beta_activation_remote_step_failed")
    return result.stdout


def render_api_environment(raw: bytes) -> bytes:
    """Set the two explicit beta v3 switches while preserving every other line."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ActivationError("beta_api_env_encoding_invalid") from None
    lines = text.splitlines(keepends=True)
    targets = {
        "VPN_ENABLE_WINDOW_TUNNELS": "true",
        "VPN_WINDOW_MANIFEST_FILE": MANIFEST,
    }
    found: dict[str, int] = {key: 0 for key in targets}
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        key, separator, value = stripped.partition("=")
        if separator and key in targets:
            found[key] += 1
            if found[key] > 1:
                raise ActivationError("beta_api_env_duplicate_window_setting")
            expected_old = {
                "VPN_ENABLE_WINDOW_TUNNELS": {"false", "0"},
                "VPN_WINDOW_MANIFEST_FILE": {""},
            }
            if value.strip().strip("\"'") not in expected_old[key]:
                raise ActivationError("beta_api_env_window_setting_unexpected")
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            rendered.append(f"{key}={targets[key]}{ending}")
        else:
            rendered.append(line)
    for key, value in targets.items():
        if not found[key]:
            if rendered and not rendered[-1].endswith(("\n", "\r")):
                rendered[-1] += "\n"
            rendered.append(f"{key}={value}\n")
    return "".join(rendered).encode("utf-8")


API_PREFLIGHT = r"""
import json, os, stat, subprocess, sys, urllib.request
from pathlib import Path
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine, transaction
from vpn_api.db.models import PeerConnection, VPNNode
from vpn_api.db.url import parse_local_database_url
from vpn_api.tunnels.window_profiles import WindowManifest
from sqlalchemy import select, func
node_id = UUID(sys.argv[1])
env = Path("/etc/detourvpn-beta/api.env")
info = env.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
    raise SystemExit(40)
settings = Settings(_env_file=env)
url = parse_local_database_url(settings.database_url)
if not (settings.environment == "closed" and settings.enable_auth and settings.enable_mini_app and settings.beta_invites_only and not settings.enable_window_tunnels and settings.window_manifest_file is None and url.database == "detourvpn_beta" and url.username == "vpn_api" and url.host in {"localhost", "127.0.0.1"} and url.port == 25433):
    raise SystemExit(41)
ready = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-api.service"], capture_output=True, text=True, check=False)
if ready.returncode != 0 or ready.stdout.strip() != "active": raise SystemExit(42)
try:
    with urllib.request.urlopen("http://127.0.0.1:28000/api/v1/ready", timeout=4) as response:
        if response.status != 200: raise SystemExit(43)
except Exception:
    raise SystemExit(43) from None
manifest_path = Path("/etc/detourvpn-beta/window-manifest.json")
manifest_info = manifest_path.lstat()
if not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_uid != 0 or stat.S_IMODE(manifest_info.st_mode) != 0o640 or manifest_info.st_nlink != 1:
    raise SystemExit(44)
manifest = WindowManifest.model_validate_json(manifest_path.read_bytes())
if len(manifest.nodes) != 1 or manifest.nodes[0].node_id != node_id:
    raise SystemExit(45)
caddy = Path("/etc/caddy/Caddyfile").read_text(errors="ignore")
for route in ("/api/internal/v3/windows/inventory", "/api/internal/v3/windows/start", "/api/internal/v3/windows/poll"):
    if route not in caddy: raise SystemExit(46)
if "95.81.73.183" not in caddy: raise SystemExit(47)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node = session.scalar(select(VPNNode).where(VPNNode.id == node_id).with_for_update())
        active = session.scalar(select(func.count()).select_from(PeerConnection).where(PeerConnection.node_id == node_id, PeerConnection.state != "closed"))
        if node is None or (node.name, node.region, node.status) != ("detourvpn-beta-helsinki", "helsinki", "disabled") or active:
            raise SystemExit(48)
finally: engine.dispose()
print("beta_activation_preflight_passed")
"""

API_ENABLE = r"""
import hashlib, json, os, stat, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from vpn_api.config import Settings
from vpn_api.db.url import parse_local_database_url
env = Path("/etc/detourvpn-beta/api.env")
info = env.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
    raise SystemExit(40)
settings = Settings(_env_file=env)
url = parse_local_database_url(settings.database_url)
if not (settings.environment == "closed" and settings.enable_auth and settings.enable_mini_app and settings.beta_invites_only and not settings.enable_window_tunnels and settings.window_manifest_file is None and url.database == "detourvpn_beta" and url.username == "vpn_api" and url.host in {"localhost", "127.0.0.1"} and url.port == 25433):
    raise SystemExit(41)
key = "VPN_ENABLE_WINDOW_TUNNELS"
manifest_key = "VPN_WINDOW_MANIFEST_FILE"
expected = {key: "true", manifest_key: "/etc/detourvpn-beta/window-manifest.json"}
old = env.read_bytes()
try: text = old.decode("utf-8")
except UnicodeDecodeError: raise SystemExit(42) from None
lines = text.splitlines(keepends=True)
counts = {key: 0, manifest_key: 0}
for line in lines:
    stripped = line.strip()
    name, sep, value = stripped.partition("=")
    if sep and name in counts:
        counts[name] += 1
        allowed = {key: {"false", "0"}, manifest_key: {""}}[name]
        if counts[name] > 1 or value.strip().strip("\"'") not in allowed: raise SystemExit(43)
rendered = []
for line in lines:
    stripped = line.strip()
    name, sep, _value = stripped.partition("=")
    if sep and name in expected:
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        rendered.append(name + "=" + expected[name] + ending)
    else: rendered.append(line)
for name, value in expected.items():
    if not counts[name]:
        if rendered and not rendered[-1].endswith(("\n", "\r")): rendered[-1] += "\n"
        rendered.append(name + "=" + value + "\n")
backup = env.with_name("api.env.pre-node-activation-" + sys.argv[1])
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(backup, flags, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(old); output.flush(); os.fsync(output.fileno())
temp = env.with_name(".api.env.activation-" + sys.argv[1])
fd = os.open(temp, flags, 0o600)
try:
    os.fchown(fd, info.st_uid, info.st_gid); os.fchmod(fd, stat.S_IMODE(info.st_mode))
    with os.fdopen(fd, "wb", closefd=True) as output:
        output.write("".join(rendered).encode("utf-8")); output.flush(); os.fsync(output.fileno())
    os.replace(temp, env)
    dfd=os.open(env.parent, os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
    try: os.fsync(dfd)
    finally: os.close(dfd)
except BaseException:
    try: temp.unlink()
    except OSError: pass
    raise
print("beta_api_env_staged backup=" + backup.name)
"""

API_RESTART = r"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from vpn_api.config import Settings
from vpn_api.tunnels.window_profiles import load_manifest
result=subprocess.run(["/usr/bin/systemctl","restart","detourvpn-beta-api.service"],capture_output=True,check=False)
if result.returncode: raise SystemExit(40)
ready=False
for _ in range(30):
    try:
        with urllib.request.urlopen("http://127.0.0.1:28000/api/v1/ready",timeout=2) as response:
            if response.status == 200: ready=True; break
    except Exception: pass
    time.sleep(1)
if not ready: raise SystemExit(41)
settings=Settings(_env_file="/etc/detourvpn-beta/api.env")
if not settings.enable_window_tunnels or settings.window_manifest_file != Path("/etc/detourvpn-beta/window-manifest.json"):
    raise SystemExit(42)
manifest=load_manifest(settings.window_manifest_file)
if len(manifest.nodes)!=1 or str(manifest.nodes[0].node_id)!="1d230f49-ed36-45f2-91ea-86fe212346f8": raise SystemExit(43)
print("beta_api_window_runtime_ready")
"""

API_ACTIVATE_ROW = r"""
import os, sys
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from sqlalchemy import func, select
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine, transaction
from vpn_api.db.models import PeerConnection, VPNNode
from vpn_api.db.url import parse_local_database_url
node_id=UUID(sys.argv[1])
settings=Settings(_env_file="/etc/detourvpn-beta/api.env")
url=parse_local_database_url(settings.database_url)
if not (settings.enable_window_tunnels and settings.window_manifest_file is not None and url.database=="detourvpn_beta" and url.username=="vpn_api" and url.host in {"localhost","127.0.0.1"} and url.port==25433): raise SystemExit(40)
engine=create_database_engine(settings)
try:
 with transaction(engine) as session:
  node=session.scalar(select(VPNNode).where(VPNNode.id==node_id).with_for_update())
  active=session.scalar(select(func.count()).select_from(PeerConnection).where(PeerConnection.node_id==node_id,PeerConnection.state!="closed"))
  if node is None or (node.name,node.region,node.status)!=("detourvpn-beta-helsinki","helsinki","disabled") or active: raise SystemExit(41)
  node.status="active"
finally: engine.dispose()
print("beta_node_enabled")
"""

NODE_START = r"""
import subprocess, sys, time
state=subprocess.run(["/usr/bin/systemctl","is-active","detourvpn-beta-node-agent.service"],capture_output=True,text=True,check=False)
if state.returncode!=3 or state.stdout.strip()!="inactive": raise SystemExit(39)
start=subprocess.run(["/usr/bin/systemctl","start","detourvpn-beta-node-agent.service"],capture_output=True,check=False)
if start.returncode: raise SystemExit(40)
for _ in range(15):
    state=subprocess.run(["/usr/bin/systemctl","is-active","detourvpn-beta-node-agent.service"],capture_output=True,text=True,check=False)
    if state.returncode==0 and state.stdout.strip()=="active":
        print("beta_node_agent_active")
        raise SystemExit(0)
    time.sleep(1)
raise SystemExit(41)
"""

NODE_PREFLIGHT = r"""
import os, stat, subprocess
from pathlib import Path
root=Path("/etc/detourvpn-node")
for name in ("agent.json","credential","slots.json"):
 p=root/name;i=p.lstat()
 if not stat.S_ISREG(i.st_mode) or i.st_uid!=0 or stat.S_IMODE(i.st_mode)!=0o600 or i.st_nlink!=1: raise SystemExit(40)
for unit in ("detourvpn-beta-slot-bootstrap.service","detourvpn-beta-core@1.service"):
 state=subprocess.run(["/usr/bin/systemctl","is-active",unit],capture_output=True,text=True,check=False)
 if state.returncode!=0 or state.stdout.strip()!="active": raise SystemExit(41)
agent=subprocess.run(["/usr/bin/systemctl","show","detourvpn-beta-node-agent.service","--property=LoadState,ActiveState,FragmentPath,User,ExecStart"],capture_output=True,text=True,check=False)
props=dict(line.split("=",1) for line in agent.stdout.splitlines() if "=" in line)
if props.get("LoadState")!="loaded" or props.get("ActiveState")!="inactive" or props.get("FragmentPath")!="/etc/systemd/system/detourvpn-beta-node-agent.service" or props.get("User")!="root" or "/opt/detourvpn-node/window_daemon.py" not in props.get("ExecStart",""):
 raise SystemExit(42)
for root in sorted(Path("/opt/detourvpn-node").glob("payload-*")):
 if root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.lstat().st_mode)!=0o700: raise SystemExit(43)
 for current,dirs,files in os.walk(root,followlinks=False):
  for name in dirs+files:
   p=Path(current)/name;i=p.lstat()
   if i.st_uid!=0 or stat.S_ISLNK(i.st_mode) or stat.S_IMODE(i.st_mode)!=(0o700 if stat.S_ISDIR(i.st_mode) else 0o600): raise SystemExit(44)
print("beta_node_activation_preflight_passed")
"""

NODE_ACTIVE_CHECK = r"""
import subprocess
state=subprocess.run(["/usr/bin/systemctl","is-active","detourvpn-beta-node-agent.service"],capture_output=True,text=True,check=False)
if state.returncode!=0 or state.stdout.strip()!="active": raise SystemExit(40)
print("beta_node_agent_active")
"""

API_WAIT_RUNTIME = r"""
import json, os, sys, time
from uuid import UUID
sys.path.insert(0,"/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key,None)
from sqlalchemy import select
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine,transaction
from vpn_api.db.models import VPNNode
node_id=UUID(sys.argv[1]);settings=Settings(_env_file="/etc/detourvpn-beta/api.env");engine=create_database_engine(settings)
try:
    for _ in range(20):
        with transaction(engine) as session:
            node=session.scalar(select(VPNNode).where(VPNNode.id==node_id))
            if node is not None and node.status=="active" and node.peer_runtime_id is not None and node.last_seen_at is not None:
                print("beta_node_authenticated_inventory_and_poll")
                break
        time.sleep(1)
    else: raise SystemExit(40)
finally: engine.dispose()
"""

NODE_STOP = r"""
import subprocess, time
result=subprocess.run(["/usr/bin/systemctl","stop","detourvpn-beta-node-agent.service"],capture_output=True,check=False)
if result.returncode: raise SystemExit(40)
for _ in range(10):
 state=subprocess.run(["/usr/bin/systemctl","is-active","detourvpn-beta-node-agent.service"],capture_output=True,text=True,check=False)
 if state.returncode==3 and state.stdout.strip()=="inactive": print("beta_node_agent_stopped"); raise SystemExit(0)
 time.sleep(1)
raise SystemExit(41)
"""

API_ROLLBACK = r"""
import json, os, stat, subprocess, sys, time, urllib.request
from pathlib import Path
from uuid import UUID
sys.path.insert(0,"/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key,None)
from sqlalchemy import func, select
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine,transaction
from vpn_api.db.models import PeerConnection,VPNNode
from vpn_api.db.url import parse_local_database_url
node_id=UUID(sys.argv[1]);env=Path("/etc/detourvpn-beta/api.env");backup=env.with_name("api.env.pre-node-activation-"+sys.argv[2])
settings=Settings(_env_file=env);url=parse_local_database_url(settings.database_url)
if not (url.database=="detourvpn_beta" and url.username=="vpn_api" and url.host in {"localhost","127.0.0.1"} and url.port==25433): raise SystemExit(41)
engine=create_database_engine(settings)
try:
 with transaction(engine) as session:
  node=session.scalar(select(VPNNode).where(VPNNode.id==node_id).with_for_update())
  active=session.scalar(select(func.count()).select_from(PeerConnection).where(PeerConnection.node_id==node_id,PeerConnection.state!="closed"))
  if active: raise SystemExit(42)
  if node is not None: node.status="disabled"
finally: engine.dispose()
if backup.exists():
 info=backup.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1: raise SystemExit(40)
 original=backup.read_bytes()
 current=env.lstat();temp=env.with_name(".api.env.rollback-"+sys.argv[2]);flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0)
 fd=os.open(temp,flags,0o600)
 try:
  os.fchown(fd,current.st_uid,current.st_gid);os.fchmod(fd,stat.S_IMODE(current.st_mode))
  with os.fdopen(fd,"wb",closefd=True) as out: out.write(original);out.flush();os.fsync(out.fileno())
  os.replace(temp,env);dfd=os.open(env.parent,os.O_RDONLY|getattr(os,"O_DIRECTORY",0));os.fsync(dfd);os.close(dfd)
 except BaseException:
  try: temp.unlink()
  except OSError: pass
  raise
result=subprocess.run(["/usr/bin/systemctl","restart","detourvpn-beta-api.service"],capture_output=True,check=False)
if result.returncode: raise SystemExit(43)
ready=False
for _ in range(30):
 try:
  with urllib.request.urlopen("http://127.0.0.1:28000/api/v1/ready",timeout=2) as response:
   if response.status==200: ready=True;break
 except Exception: pass
 time.sleep(1)
if not ready: raise SystemExit(44)
settings=Settings(_env_file=env)
if settings.enable_window_tunnels or settings.window_manifest_file is not None: raise SystemExit(45)
print("beta_activation_rolled_back")
"""

API_ROLLBACK_PREFLIGHT = r"""
import os, stat, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0,"/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key,None)
from sqlalchemy import func,select
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine,transaction
from vpn_api.db.models import PeerConnection,VPNNode
from vpn_api.db.url import parse_local_database_url
node_id=UUID(sys.argv[1]);tag=sys.argv[2]
if len(tag)!=32 or any(ch not in "0123456789abcdef" for ch in tag): raise SystemExit(40)
env=Path("/etc/detourvpn-beta/api.env");backup=env.with_name("api.env.pre-node-activation-"+tag)
info=backup.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1: raise SystemExit(41)
settings=Settings(_env_file=env);url=parse_local_database_url(settings.database_url)
if not (url.database=="detourvpn_beta" and url.username=="vpn_api" and url.host in {"localhost","127.0.0.1"} and url.port==25433): raise SystemExit(42)
engine=create_database_engine(settings)
try:
 with transaction(engine) as session:
  node=session.scalar(select(VPNNode).where(VPNNode.id==node_id).with_for_update())
  active=session.scalar(select(func.count()).select_from(PeerConnection).where(PeerConnection.node_id==node_id,PeerConnection.state!="closed"))
  if node is None or node.status!="active" or active: raise SystemExit(43)
finally:engine.dispose()
print("beta_rollback_preflight_passed")
"""


def _run(host: str, interpreter: str, source: str, args: tuple[str, ...] = ()) -> bytes:
    try:
        return _ssh(host, _python_command(interpreter, source, args))
    except Exception:
        raise ActivationError("beta_activation_remote_step_failed") from None


def _expect(output: bytes, marker: bytes, code: str) -> None:
    if output.strip() != marker:
        raise ActivationError(code)


def activate() -> dict[str, str]:
    backup_tag = uuid4().hex
    node_id = str(NODE_ID)
    api_py = "/opt/detourvpn-beta/.venv/bin/python"
    node_py = "/usr/bin/python3"
    api_stage_attempted = False
    try:
        _expect(
            _run(NODE_HOST, node_py, NODE_PREFLIGHT),
            b"beta_node_activation_preflight_passed",
            "beta_activation_node_preflight_failed",
        )
        _expect(
            _run(API_HOST, api_py, API_PREFLIGHT, (node_id,)),
            b"beta_activation_preflight_passed",
            "beta_activation_api_preflight_failed",
        )
        api_stage_attempted = True
        enabled = _run(API_HOST, api_py, API_ENABLE, (backup_tag,))
        if b"beta_api_env_staged backup=api.env.pre-node-activation-" not in enabled:
            raise ActivationError("beta_api_env_stage_unverified")
        _expect(
            _run(API_HOST, api_py, API_RESTART),
            b"beta_api_window_runtime_ready",
            "beta_api_restart_verification_failed",
        )
        _expect(
            _run(API_HOST, api_py, API_ACTIVATE_ROW, (node_id,)),
            b"beta_node_enabled",
            "beta_node_enable_failed",
        )
        _expect(
            _run(NODE_HOST, node_py, NODE_START),
            b"beta_node_agent_active",
            "beta_node_agent_start_failed",
        )
        _expect(
            _run(API_HOST, api_py, API_WAIT_RUNTIME, (node_id,)),
            b"beta_node_authenticated_inventory_and_poll",
            "beta_node_authentication_failed",
        )
        _expect(
            _run(NODE_HOST, node_py, NODE_ACTIVE_CHECK),
            b"beta_node_agent_active",
            "beta_node_agent_not_active",
        )
        return {
            "node_id": node_id,
            "status": "active",
            "node_agent": "active",
            "api_window_tunnels": "enabled",
            "rollback_backup": "api.env.pre-node-activation-" + backup_tag,
        }
    except Exception as error:
        if not api_stage_attempted:
            raise ActivationError(
                str(error) if isinstance(error, ActivationError) else "beta_activation_failed"
            ) from None
        try:
            _expect(
                _run(NODE_HOST, node_py, NODE_STOP),
                b"beta_node_agent_stopped",
                "beta_rollback_node_stop_failed",
            )
            _expect(
                _run(API_HOST, api_py, API_ROLLBACK, (node_id, backup_tag)),
                b"beta_activation_rolled_back",
                "beta_rollback_api_restore_failed",
            )
        except Exception:
            raise ActivationError("beta_activation_failed_rollback_incomplete") from None
        raise ActivationError("beta_activation_failed_rolled_back") from None


def rollback(backup_tag: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{32}", backup_tag) is None:
        raise ActivationError("beta_rollback_tag_invalid")
    _expect(
        _run(
            API_HOST,
            "/opt/detourvpn-beta/.venv/bin/python",
            API_ROLLBACK_PREFLIGHT,
            (str(NODE_ID), backup_tag),
        ),
        b"beta_rollback_preflight_passed",
        "beta_rollback_preflight_failed",
    )
    _expect(
        _run(NODE_HOST, "/usr/bin/python3", NODE_STOP),
        b"beta_node_agent_stopped",
        "beta_rollback_node_stop_failed",
    )
    _expect(
        _run(
            API_HOST,
            "/opt/detourvpn-beta/.venv/bin/python",
            API_ROLLBACK,
            (str(NODE_ID), backup_tag),
        ),
        b"beta_activation_rolled_back",
        "beta_rollback_api_restore_failed",
    )
    return {
        "node_id": str(NODE_ID),
        "status": "disabled",
        "node_agent": "inactive",
        "api_window_tunnels": "disabled",
        "rollback_backup": "api.env.pre-node-activation-" + backup_tag,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback-tag")
    arguments = parser.parse_args(argv)
    try:
        result = rollback(arguments.rollback_tag) if arguments.rollback_tag else activate()
    except ActivationError as error:
        print(
            json.dumps(
                {
                    "activated": False,
                    "rolled_back": False,
                    "action": "rollback" if arguments.rollback_tag else "activate",
                    "error": str(error),
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {"rolled_back" if arguments.rollback_tag else "activated": True, **result},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

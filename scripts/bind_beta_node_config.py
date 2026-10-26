"""Stage beta node credentials and manifest through pinned SSH without local secret files."""

# E501 is disabled for this file because it embeds fixed remote Python programs.
# Those programs are reviewed and syntax-checked as whole scripts, not formatted as host code.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.tunnels.window_profiles import WindowManifest  # noqa: E402

API_HOST = "193.68.88.239"
NODE_HOST = "95.81.73.183"
API_ORIGIN = "https://193.68.88.239"
SLOT_HOST = "95.81.73.183"
PROVISION_KEY = UUID("9050ca5a-4517-4027-aef7-8f26a6f7f9f8")
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


class BindingError(RuntimeError):
    """Fixed error codes only; never include a credential or key value."""


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def build_manifest(node_id: UUID, slots_raw: bytes) -> bytes:
    try:
        source = json.loads(slots_raw)
        slots = source["slots"]
        if source.get("version") != 1 or not isinstance(slots, list) or not 1 <= len(slots) <= 16:
            raise ValueError
        rendered = []
        for expected_slot, slot in enumerate(slots, start=1):
            if not isinstance(slot, dict) or slot.get("slot_id") != expected_slot:
                raise ValueError
            if slot["endpoint"].split(":", 1)[0] != SLOT_HOST:
                raise ValueError
            rendered.append(
                {
                    "slot_id": expected_slot,
                    "endpoint": slot["endpoint"],
                    "server_public_key": slot["server_public_key"],
                    "header_protection_key": slot["header_protection_key"],
                }
            )
        manifest = {"version": 3, "nodes": [{"node_id": str(node_id), "slots": rendered}]}
        WindowManifest.model_validate(manifest)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise BindingError("beta_node_manifest_invalid") from None
    return _canonical_json(manifest)


def build_node_payload(node_id: UUID, credential_data: bytes, slot_count: int) -> bytes:
    try:
        saved = json.loads(credential_data)
        credential = saved["credential"]
        if (
            set(saved) != {"version", "node_id", "credential"}
            or saved["version"] != 1
            or UUID(saved["node_id"]) != node_id
            or not isinstance(credential, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None
            or not 1 <= slot_count <= 16
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise BindingError("beta_node_credential_invalid") from None
    return _canonical_json(
        {
            "node_id": str(node_id),
            "api_origin": API_ORIGIN,
            "slot_count": slot_count,
            "credential": credential,
        }
    )


def _python_command(interpreter: str, source: str, arguments: tuple[str, ...] = ()) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    code = f"import base64;exec(base64.b64decode('{encoded}'))"
    parts = [shlex.quote(interpreter), "-c", shlex.quote(code)]
    parts.extend(shlex.quote(value) for value in arguments)
    return " ".join(parts)


def _ssh(host: str, command: str, payload: bytes | None = None) -> bytes:
    executable = shutil.which("ssh.exe") or shutil.which("ssh")
    if executable is None or not SSH_KEY.is_file():
        raise BindingError("beta_binding_ssh_unavailable")
    result = subprocess.run(  # noqa: S603 - fixed SSH hosts and quoted fixed remote programs
        [executable, *SSH_OPTIONS, f"root@{host}", command],
        input=payload,
        capture_output=True,
        timeout=40,
        check=False,
    )
    if result.returncode != 0:
        raise BindingError("beta_binding_remote_step_failed")
    return result.stdout


READ_CREDENTIAL = r"""
import json, os, re, stat, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine, transaction
from vpn_api.db.models import VPNNode
from vpn_api.db.url import parse_local_database_url
from sqlalchemy import select
key = UUID(sys.argv[1])
path = Path("/run/detourvpn-beta") / f"node-credential-{key}.json"
directory = path.parent.lstat()
if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or stat.S_IMODE(directory.st_mode) != 0o700:
    raise SystemExit(40)
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096:
    raise SystemExit(41)
raw = path.read_bytes()
saved = json.loads(raw)
node_id = UUID(saved["node_id"])
credential = saved["credential"]
if saved.get("version") != 1 or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
    raise SystemExit(42)
settings = Settings(_env_file="/etc/detourvpn-beta/api.env")
url = parse_local_database_url(settings.database_url)
if not (settings.environment == "closed" and settings.enable_auth and settings.enable_mini_app and settings.beta_invites_only and not settings.enable_window_tunnels and url.database == "detourvpn_beta" and url.username == "vpn_api" and url.host in {"localhost", "127.0.0.1"} and url.port == 25433):
    raise SystemExit(43)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node = session.scalar(select(VPNNode).where(VPNNode.id == node_id).with_for_update())
        if node is None or (node.name, node.region, node.status) != ("detourvpn-beta-helsinki", "helsinki", "disabled"):
            raise SystemExit(44)
finally:
    engine.dispose()
sys.stdout.buffer.write(raw)
"""

READ_SLOTS = r"""
import json, stat, sys
from pathlib import Path
p = Path("/etc/detourvpn-node/slots.json")
info = p.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 65536:
    raise SystemExit(41)
data = json.loads(p.read_bytes())
if data.get("version") != 1 or not isinstance(data.get("slots"), list):
    raise SystemExit(42)
filtered = []
for slot in data["slots"]:
    filtered.append({key: slot[key] for key in ("slot_id", "endpoint", "server_public_key", "header_protection_key")})
sys.stdout.write(json.dumps({"version": 1, "slots": filtered}, separators=(",", ":")))
"""

CLEANUP_CREDENTIAL = r"""
import hashlib, os, stat, sys
from pathlib import Path
from uuid import UUID
key = UUID(sys.argv[1])
expected = sys.argv[2]
directory = Path("/run/detourvpn-beta")
directory_info = directory.lstat()
if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != 0 or stat.S_IMODE(directory_info.st_mode) != 0o700:
    raise SystemExit(40)
path = directory / f"node-credential-{key}.json"
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096:
    raise SystemExit(41)
if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
    raise SystemExit(42)
path.unlink()
fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try: os.fsync(fd)
finally: os.close(fd)
sys.stdout.write("credential_source_removed\n")
"""

STAGE_NODE = r"""
import json, os, re, stat, sys
from pathlib import Path
from uuid import UUID
payload = json.loads(sys.stdin.buffer.read(8193))
node_id = UUID(payload["node_id"])
credential = payload["credential"]
origin = payload["api_origin"]
if set(payload) != {"node_id", "credential", "api_origin", "slot_count"} or origin != "https://193.68.88.239" or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
    raise SystemExit(41)
root = Path("/etc/detourvpn-node")
root_info = root.lstat()
if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != 0 or root_info.st_mode & 0o077:
    raise SystemExit(42)
slots_path = root / "slots.json"
slots_info = slots_path.lstat()
if not stat.S_ISREG(slots_info.st_mode) or slots_info.st_uid != 0 or slots_info.st_mode & 0o077 or slots_info.st_nlink != 1:
    raise SystemExit(43)
slots = json.loads(slots_path.read_bytes())
if payload["slot_count"] != len(slots.get("slots", [])) or not 1 <= payload["slot_count"] <= 16:
    raise SystemExit(44)
agent = (json.dumps({"node_id": str(node_id), "api_origin": origin, "slot_count": payload["slot_count"]}, sort_keys=True, indent=2) + "\n").encode("ascii")
credential_bytes = (credential + "\n").encode("ascii")
files = ((root / "agent.json", agent), (root / "credential", credential_bytes))
created = []
try:
    for path, data in files:
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1 or path.read_bytes() != data:
                raise SystemExit(45)
            continue
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"): flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        created.append(path)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(path, 0o600, follow_symlinks=False)
except BaseException:
    for path in created:
        try: path.unlink()
        except OSError: pass
    raise
sys.stdout.write("node_configuration_staged\n")
"""

STAGE_API_MANIFEST = r"""
import grp, json, os, stat, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine, transaction
from vpn_api.db.models import VPNNode
from vpn_api.db.url import parse_local_database_url
from vpn_api.tunnels.window_profiles import WindowManifest
from sqlalchemy import select
node_id = UUID(sys.argv[1])
raw = sys.stdin.buffer.read(65537)
manifest = WindowManifest.model_validate_json(raw)
if len(raw) > 65536 or len(manifest.nodes) != 1 or manifest.nodes[0].node_id != node_id:
    raise SystemExit(41)
for slot in manifest.nodes[0].slots:
    host, port = slot.endpoint.rsplit(":", 1)
    if host != "95.81.73.183" or port != str(51900 + slot.slot_id):
        raise SystemExit(42)
env_path = Path("/etc/detourvpn-beta/api.env")
env_info = env_path.lstat()
if not stat.S_ISREG(env_info.st_mode) or env_info.st_uid != 0 or env_info.st_mode & 0o077 or env_info.st_nlink != 1 or env_info.st_size > 65536:
    raise SystemExit(43)
settings = Settings(_env_file=env_path)
url = parse_local_database_url(settings.database_url)
if not (settings.environment == "closed" and settings.enable_auth and settings.enable_mini_app and settings.beta_invites_only and not settings.enable_window_tunnels and settings.window_manifest_file is None and url.database == "detourvpn_beta" and url.username == "vpn_api" and url.host in {"localhost", "127.0.0.1"} and url.port == 25433):
    raise SystemExit(44)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node = session.scalar(select(VPNNode).where(VPNNode.id == node_id).with_for_update())
        if node is None or (node.name, node.region, node.status) != ("detourvpn-beta-helsinki", "helsinki", "disabled"):
            raise SystemExit(45)
finally:
    engine.dispose()
directory = Path("/etc/detourvpn-beta")
directory_info = directory.lstat()
if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != 0 or directory_info.st_mode & 0o022:
    raise SystemExit(46)
target = directory / "window-manifest.json"
try:
    existing = target.lstat()
except FileNotFoundError:
    existing = None
if existing is not None:
    if not stat.S_ISREG(existing.st_mode) or existing.st_uid != 0 or existing.st_mode & 0o027 or existing.st_nlink != 1 or target.read_bytes() != raw:
        raise SystemExit(47)
else:
    group = grp.getgrnam("detourvpn-beta").gr_gid
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"): flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o640)
    try:
        os.fchown(fd, 0, group)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "wb", closefd=True) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        try: target.unlink()
        except OSError: pass
        raise
sys.stdout.write("api_manifest_staged\n")
"""

VERIFY_NODE = r"""
import json, stat, sys
import subprocess
from pathlib import Path
from uuid import UUID
root = Path("/etc/detourvpn-node")
for name in ("agent.json", "credential"):
    path = root / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise SystemExit(41)
config = json.loads((root / "agent.json").read_bytes())
if UUID(config["node_id"]) != UUID(sys.argv[1]) or config["api_origin"] != "https://193.68.88.239":
    raise SystemExit(42)
state = subprocess.run(["/usr/bin/systemctl", "is-active", "detourvpn-beta-node-agent.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
if state.returncode != 3 or state.stdout.strip() != b"inactive":
    raise SystemExit(43)
sys.stdout.write("node_private_files_verified; agent_not_started\n")
"""

VERIFY_API = r"""
import hashlib, json, os, stat, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, "/opt/detourvpn-beta/services/api/src")
for key in list(os.environ):
    if key.startswith("VPN_"): os.environ.pop(key, None)
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine, transaction
from vpn_api.db.models import VPNNode
from vpn_api.db.url import parse_local_database_url
from vpn_api.tunnels.window_profiles import WindowManifest
from sqlalchemy import select
node_id = UUID(sys.argv[1])
path = Path("/etc/detourvpn-beta/window-manifest.json")
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o640 or info.st_nlink != 1 or info.st_size > 65536:
    raise SystemExit(41)
raw = path.read_bytes()
manifest = WindowManifest.model_validate_json(raw)
if len(manifest.nodes) != 1 or manifest.nodes[0].node_id != node_id:
    raise SystemExit(42)
settings = Settings(_env_file="/etc/detourvpn-beta/api.env")
url = parse_local_database_url(settings.database_url)
if settings.enable_window_tunnels or settings.window_manifest_file is not None or url.database != "detourvpn_beta" or url.username != "vpn_api" or url.host not in {"localhost", "127.0.0.1"} or url.port != 25433:
    raise SystemExit(43)
engine = create_database_engine(settings)
try:
    with transaction(engine) as session:
        node = session.scalar(select(VPNNode).where(VPNNode.id == node_id).with_for_update())
        if node is None or (node.name, node.region, node.status) != ("detourvpn-beta-helsinki", "helsinki", "disabled"):
            raise SystemExit(44)
finally: engine.dispose()
print("api_manifest_verified; tunnels_disabled; node_disabled; sha256=" + hashlib.sha256(raw).hexdigest())
"""


def bind(provision_key: UUID = PROVISION_KEY) -> dict[str, str]:
    credential_path_key = str(provision_key)
    try:
        slots_raw = _ssh(NODE_HOST, _python_command("/usr/bin/python3", READ_SLOTS))
        slots = json.loads(slots_raw)
    except Exception:
        raise BindingError("beta_node_slot_read_failed") from None
    slot_count = len(slots.get("slots", []))
    if not 1 <= slot_count <= 16:
        raise BindingError("beta_node_slots_invalid")

    try:
        credential_raw = _ssh(
            API_HOST,
            _python_command(
                "/opt/detourvpn-beta/.venv/bin/python", READ_CREDENTIAL, (credential_path_key,)
            ),
        )
    except Exception:
        raise BindingError("beta_api_credential_read_failed") from None
    try:
        credential_record = json.loads(credential_raw)
        node_id = UUID(credential_record["node_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise BindingError("beta_node_credential_invalid") from None
    manifest_raw = build_manifest(node_id, slots_raw)
    node_payload = build_node_payload(node_id, credential_raw, slot_count)

    try:
        node_result = _ssh(
            NODE_HOST,
            _python_command("/usr/bin/python3", STAGE_NODE),
            node_payload,
        )
    except Exception:
        raise BindingError("beta_node_config_stage_failed") from None
    if node_result != b"node_configuration_staged\n":
        raise BindingError("beta_node_stage_unverified")

    try:
        api_result = _ssh(
            API_HOST,
            _python_command(
                "/opt/detourvpn-beta/.venv/bin/python", STAGE_API_MANIFEST, (str(node_id),)
            ),
            manifest_raw,
        )
    except Exception:
        raise BindingError("beta_api_manifest_stage_failed") from None
    if api_result != b"api_manifest_staged\n":
        raise BindingError("beta_api_manifest_stage_unverified")

    try:
        node_verify = _ssh(
            NODE_HOST,
            _python_command("/usr/bin/python3", VERIFY_NODE, (str(node_id),)),
        )
    except Exception:
        raise BindingError("beta_node_config_verify_failed") from None
    if node_verify != b"node_private_files_verified; agent_not_started\n":
        raise BindingError("beta_node_stage_verification_failed")
    try:
        api_verify = _ssh(
            API_HOST,
            _python_command("/opt/detourvpn-beta/.venv/bin/python", VERIFY_API, (str(node_id),)),
        )
    except Exception:
        raise BindingError("beta_api_manifest_verify_failed") from None
    if b"api_manifest_verified; tunnels_disabled; node_disabled; sha256=" not in api_verify:
        raise BindingError("beta_api_stage_verification_failed")

    digest = hashlib.sha256(credential_raw).hexdigest()
    try:
        cleanup = _ssh(
            API_HOST,
            _python_command(
                "/opt/detourvpn-beta/.venv/bin/python",
                CLEANUP_CREDENTIAL,
                (credential_path_key, digest),
            ),
        )
    except Exception:
        raise BindingError("beta_credential_cleanup_failed") from None
    if cleanup != b"credential_source_removed\n":
        raise BindingError("beta_credential_cleanup_unverified")
    return {"node_id": str(node_id), "status": "disabled", "credential_source": "removed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provision-key", type=UUID, default=PROVISION_KEY)
    arguments = parser.parse_args(argv)
    try:
        result = bind(arguments.provision_key)
    except BindingError as error:
        print(json.dumps({"bound": False, "error": str(error)}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"bound": False, "error": "beta_node_binding_failed"}), file=sys.stderr)
        return 1
    print(json.dumps({"bound": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

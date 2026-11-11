"""Install the beta VPN node software on a prepared candidate host (ADR-094, stage 2a).

Dry run by default: runs the read-only candidate preflight, builds the pinned package and fresh slot
keys in memory and prints a plan. With ``--execute`` it uploads the package over SSH and runs one
fixed program on the NEW host only. It never touches the beta API host, the database or the
existing Helsinki node, and it does not start the node agent. Slot private keys exist only in
memory, in the SSH stream and in root-private files on the new host; they are never printed.

The layout mirrors the working node: uv 0.11.17, uv-managed CPython 3.13.12, the five hash-pinned
packages, the three reviewed systemd units and one closed AWG slot per ``--slots``.
"""

# E501 is disabled because the file embeds one fixed remote program.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from scripts.beta_nodes import BetaNode, InventoryError, load_inventory
from scripts.check_candidate_node import CheckError, evaluate, run_remote
from scripts.package_beta_node import PackageError, build_package

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "node-agent"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from beta_slot_config import ConfigError, render_configs  # noqa: E402

DEFAULT_INVENTORY = ROOT / "infra" / "beta" / "nodes.json"
# Pins taken from the working beta node (uv binary and uv-managed CPython 3.13.12 interpreter).
UV_VERSION = "0.11.17"
UV_URL = (
    f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
    "uv-x86_64-unknown-linux-gnu.tar.gz"
)
UV_SHA256 = "8ac91b3913a96c6d98d65b2fc6996064c85d0dc42a626977d337046be796c75d"
PYTHON_VERSION = "3.13.12"
PYTHON_SHA256 = "fed567fcbdacc14612dddd5f29a3203fbe5c93f50b926f15e7a926cf668822a5"
# Plain-text freeze of the working node's environment, in uv's own order.
EXPECTED_FREEZE = (
    "annotated-types==0.8.0 pydantic==2.14.1 pydantic-core==2.50.1 "
    "typing-extensions==4.16.0 typing-inspection==0.4.4"
)
STEP_PATTERN = re.compile(r"^(step|failed_step|diag)=([a-z0-9_]{1,64})$")
MAX_SLOTS = 16


class InstallError(RuntimeError):
    """Fixed error codes only; never include keys, addresses or raw remote output."""


REMOTE_PROGRAM = r"""
set -Eeuo pipefail
umask 077
run="$1"; uv_url="$2"; uv_sha="$3"; py_version="$4"; py_sha="$5"; slots="$6"; freeze_expected="$7"
[[ "$run" =~ ^[0-9a-f]{32}$ ]] || exit 2
[[ "$slots" =~ ^([1-9]|1[0-6])$ ]] || exit 2
stage="/root/detourvpn-node-install-$run"
step=start; created=0; started=0
trap 'rm -rf -- "$stage"' EXIT
emit() { step="$1"; printf 'step=%s\n' "$1"; }
rollback() {
  if [ "$started" = 1 ]; then systemctl disable --now detourvpn-beta-slot-bootstrap.service >/dev/null 2>&1 || true; fi
  systemctl disable --now detourvpn-beta-latency-probe.service >/dev/null 2>&1 || true
  if [ "$created" = 1 ]; then
    rm -f -- /etc/systemd/system/detourvpn-beta-node-agent.service /etc/systemd/system/detourvpn-beta-slot-bootstrap.service '/etc/systemd/system/detourvpn-beta-core@.service' /etc/systemd/system/detourvpn-beta-latency-probe.service
    rm -rf -- /opt/detourvpn-node /etc/detourvpn-node /run/detourvpn-node
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl reset-failed 'detourvpn-beta-*' >/dev/null 2>&1 || true
  fi
}
on_error() {
  trap - ERR
  printf 'failed_step=%s\n' "$step"
  if [ "$step" = bootstrap ]; then
    # Fixed error identifiers only (no addresses, no raw log lines).
    journalctl -u detourvpn-beta-slot-bootstrap.service -n 60 --no-pager -o cat 2>/dev/null | grep -oE '"(error|stage)": "[a-z_]{3,60}"' | sed -E 's/"([a-z]+)": "([a-z_]+)"/diag=\1_\2/' | sort -u | head -6 || true
    systemctl show -p Result,ExecMainStatus detourvpn-beta-slot-bootstrap.service 2>/dev/null | tr 'A-Z=-' 'a-z__' | sed 's/^/diag=/' || true
  fi
  rollback
  exit 1
}
trap on_error ERR

emit checks
test "$(id -u)" = 0
test -d "$stage/pkg"
test -d "$stage/cfg"
for path in /opt/detourvpn-node /etc/detourvpn-node /run/detourvpn-node /var/lib/detourvpn-node /etc/systemd/system/detourvpn-beta-node-agent.service /etc/systemd/system/detourvpn-beta-slot-bootstrap.service '/etc/systemd/system/detourvpn-beta-core@.service' /etc/systemd/system/detourvpn-beta-latency-probe.service; do
  test ! -e "$path"
  test ! -L "$path"
done
for tool in curl tar systemctl ip nft sha256sum install; do command -v "$tool" >/dev/null; done
test -x /usr/lib/systemd/systemd-executor
test "$(cat /proc/sys/net/ipv4/ip_forward)" = 1
curl -fsSI -m 15 --proto '=https' https://github.com >/dev/null
(cd "$stage/pkg" && sha256sum --check --quiet SHA256SUMS)

emit files
created=1
install -d -m 0700 /opt/detourvpn-node /opt/detourvpn-node/current /opt/detourvpn-node/vpn_api /opt/detourvpn-node/vpn_api/tunnels /etc/detourvpn-node /etc/detourvpn-node/awg
for name in window_daemon.py beta_node_preflight.py beta_slot_bootstrap.py window_gate.py window_peer.py window_pool.py latency_probe.py requirements.txt; do
  install -m 0600 "$stage/pkg/$name" "/opt/detourvpn-node/$name"
done
for name in __init__.py tunnels/__init__.py tunnels/keys.py tunnels/awg_profile.py tunnels/v3_contracts.py tunnels/window_journal.py tunnels/window_runtime.py; do
  install -m 0600 "$stage/pkg/vpn_api/$name" "/opt/detourvpn-node/vpn_api/$name"
done
install -m 0700 "$stage/pkg/bin/awg" /opt/detourvpn-node/current/awg
install -m 0700 "$stage/pkg/bin/amneziawg-go" /opt/detourvpn-node/current/amneziawg-go

emit uv
mkdir -p "$stage/dl"
curl -fsSL --proto '=https' --tlsv1.2 -m 180 -o "$stage/dl/uv.tar.gz" "$uv_url"
tar -xzf "$stage/dl/uv.tar.gz" -C "$stage/dl"
install -m 0700 "$stage/dl/uv-x86_64-unknown-linux-gnu/uv" /opt/detourvpn-node/uv
test "$(sha256sum /opt/detourvpn-node/uv | cut -d ' ' -f 1)" = "$uv_sha"

emit python
export UV_PYTHON_INSTALL_DIR=/opt/detourvpn-node/python UV_CACHE_DIR="$stage/uv-cache" UV_NO_CONFIG=1 UV_NO_PROGRESS=1
/opt/detourvpn-node/uv python install "$py_version" --no-bin >/dev/null 2>&1
py="/opt/detourvpn-node/python/cpython-$py_version-linux-x86_64-gnu/bin/python3.13"
test "$(sha256sum "$py" | cut -d ' ' -f 1)" = "$py_sha"

emit venv
/opt/detourvpn-node/uv venv --no-project --python "$py" /opt/detourvpn-node/.venv >/dev/null 2>&1
/opt/detourvpn-node/uv pip install --python /opt/detourvpn-node/.venv/bin/python --require-hashes --no-deps --only-binary :all: -r /opt/detourvpn-node/requirements.txt >/dev/null 2>&1
freeze="$(/opt/detourvpn-node/uv pip freeze --python /opt/detourvpn-node/.venv/bin/python 2>/dev/null | tr '\n' ' ')"
test "${freeze% }" = "$freeze_expected"
/opt/detourvpn-node/.venv/bin/python -c 'import pydantic' >/dev/null

emit config
install -m 0600 "$stage/cfg/slots.json" /etc/detourvpn-node/slots.json
for index in $(seq 1 "$slots"); do
  install -m 0600 "$stage/cfg/awg/slot-$index.conf" "/etc/detourvpn-node/awg/slot-$index.conf"
done

emit units
for unit in detourvpn-beta-node-agent.service detourvpn-beta-slot-bootstrap.service 'detourvpn-beta-core@.service' detourvpn-beta-latency-probe.service; do
  install -m 0644 -o root -g root "$stage/pkg/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload

emit bootstrap
started=1
systemctl enable --now detourvpn-beta-slot-bootstrap.service >/dev/null 2>&1
for index in $(seq 1 "$slots"); do
  systemctl is-active --quiet "detourvpn-beta-core@$index.service"
  test -f "/run/detourvpn-node/slots/$index.json"
done
test "$(systemctl is-active detourvpn-beta-node-agent.service || true)" = inactive

emit probe
# ADR-102: unauthenticated UDP latency echo, capability-free, no tunnel state.
systemctl enable --now detourvpn-beta-latency-probe.service >/dev/null 2>&1
systemctl is-active --quiet detourvpn-beta-latency-probe.service

emit done
"""


def default_artifact_directory() -> Path:
    """Find the pinned binaries cache, also from a git worktree below the main checkout."""
    for parent in (ROOT, *ROOT.parents):
        candidate = parent / ".cache" / "i-010" / "artifacts"
        if candidate.is_dir():
            return candidate
    return ROOT / ".cache" / "i-010" / "artifacts"


def generate_slot_source(node: BetaNode, slots: int, pool_peers: int | None = None) -> bytes:
    """Fresh slot keys for this node. The placeholder node id is discarded; bind sets the real one.

    ``pool_peers`` (ADR-101) turns slot 1 into a pool for that many simultaneous devices.
    """
    if not 1 <= slots <= MAX_SLOTS:
        raise InstallError("install_slot_count_invalid")
    if pool_peers is not None and (slots != 1 or not 2 <= pool_peers <= 250):
        raise InstallError("install_pool_invalid")
    entries = []
    for slot_id in range(1, slots + 1):
        private = X25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        entries.append(
            {
                "slot_id": slot_id,
                "endpoint": f"{node.host}:{51900 + slot_id}",
                "server_private_key": base64.b64encode(private).decode("ascii"),
                "header_protection_key": base64.b64encode(os.urandom(32)).decode("ascii"),
            }
        )
    source = {
        "version": 1,
        "node_id": str(uuid.uuid4()),
        "api_origin": "https://192.0.2.1",
        "slots": entries,
    }
    if pool_peers is not None:
        source["pool_peers"] = pool_peers
    return json.dumps(source, sort_keys=True).encode("ascii")


def add_member(archive: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def build_archive(package_directory: Path, rendered: dict[str, bytes]) -> bytes:
    """One tar with ``pkg/…`` (the pinned package) and ``cfg/…`` (slots and AWG configs)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for path in sorted(package_directory.rglob("*")):
            if path.is_symlink():
                raise InstallError("install_package_symlink")
            if path.is_file():
                relative = path.relative_to(package_directory).as_posix()
                executable = relative.startswith("bin/")
                add_member(
                    archive, f"pkg/{relative}", path.read_bytes(), 0o700 if executable else 0o600
                )
        for name, data in sorted(rendered.items()):
            add_member(archive, f"cfg/{name}", data, 0o600)
    return buffer.getvalue()


def prepare_payload(
    node: BetaNode, slots: int, artifact_directory: Path, pool_peers: int | None = None
) -> tuple[bytes, int]:
    try:
        rendered = render_configs(generate_slot_source(node, slots, pool_peers))
        with tempfile.TemporaryDirectory(prefix="detourvpn-node-package-") as scratch:
            package = Path(scratch) / "package"
            manifest = build_package(ROOT, artifact_directory, package)
            files = {"slots.json": rendered["node_slots"]}
            files.update({f"awg/{name}": data for name, data in rendered["awg_configs"].items()})
            return build_archive(package, files), len(manifest["files"])
    except (ConfigError, PackageError):
        raise InstallError("install_payload_invalid") from None


def _ssh(
    node: BetaNode, remote: list[str], stdin: bytes, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(node.ssh_key), "-p", str(node.ssh_port),
        f"{node.ssh_user}@{node.host}", *remote,
    ]  # fmt: skip
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no local shell, fixed program on stdin.
            command, input=stdin, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("install_ssh_unavailable") from None


def parse_steps(output: bytes) -> list[str]:
    """Keep only the fixed ``step=`` / ``failed_step=`` marker lines of the remote program."""
    lines = []
    for raw in output.decode("utf-8", "replace").splitlines():
        if STEP_PATTERN.match(raw.strip()):
            lines.append(raw.strip())
    return lines


def execute(node: BetaNode, archive: bytes, slots: int) -> list[str]:
    run_id = uuid.uuid4().hex
    stage = f"/root/detourvpn-node-install-{run_id}"
    upload = _ssh(
        node,
        [f"umask 077 && mkdir {stage} && tar -x -C {stage} --no-same-owner"],
        archive,
        timeout=300,
    )
    if upload.returncode != 0:
        _ssh(node, [f"rm -rf -- {stage}"], b"", timeout=30)
        raise InstallError("install_upload_failed")
    arguments = [
        run_id,
        UV_URL,
        UV_SHA256,
        PYTHON_VERSION,
        PYTHON_SHA256,
        str(slots),
        EXPECTED_FREEZE,
    ]
    # The freeze text contains spaces, so it travels as one base-quoted word.
    arguments[-1] = "'" + arguments[-1] + "'"
    result = _ssh(
        node, ["bash", "-s", "--", *arguments], REMOTE_PROGRAM.encode("ascii"), timeout=900
    )
    steps = parse_steps(result.stdout)
    if result.returncode != 0:
        failed = next(
            (line for line in steps if line.startswith("failed_step=")), "failed_step=unknown"
        )
        diag = [line.split("=", 1)[1] for line in steps if line.startswith("diag=")]
        raise InstallError(
            f"install_failed:{failed.split('=', 1)[1]}" + (f":{','.join(diag)}" if diag else "")
        )
    if steps[-1:] != ["step=done"]:
        raise InstallError("install_unverified")
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, help="node name from the inventory")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument("--slots", type=int, default=1)
    parser.add_argument(
        "--pool-peers",
        type=int,
        default=None,
        help="ADR-101: one slot hosting this many simultaneous devices (2..250)",
    )
    parser.add_argument("--execute", action="store_true", help="really install (default: dry run)")
    parser.add_argument("--receipt", type=Path, help="write the sanitized result to a NEW file")
    arguments = parser.parse_args(argv)
    try:
        node = load_inventory(arguments.inventory).get(arguments.node)
        if node is None:
            raise InstallError("install_node_not_in_inventory")
        checks = evaluate(run_remote(node))
        if not all(item["ok"] for item in checks if item["required"]):
            raise InstallError("install_candidate_not_ready")
        archive, file_count = prepare_payload(
            node,
            arguments.slots,
            arguments.artifact_dir or default_artifact_directory(),
            arguments.pool_peers,
        )
        report: dict[str, object] = {
            "node": node.name,
            "mode": "execute" if arguments.execute else "dry_run",
            "preflight": "ready",
            "package_files": file_count,
            "slots": arguments.slots,
            "pool_peers": arguments.pool_peers,
            "archive_bytes": len(archive),
        }
        if arguments.execute:
            report["steps"] = execute(node, archive, arguments.slots)
            report["installed"] = True
            report["note"] = (
                "node agent not started; node not registered; not evidence of handshake"
            )
    except (InventoryError, CheckError, InstallError) as error:
        print(json.dumps({"installed": False, "error": str(error)}), file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2) + "\n"
    if arguments.receipt is not None:
        with arguments.receipt.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

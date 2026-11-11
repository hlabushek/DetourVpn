"""Update the node-agent code on an installed beta node, keeping its slots, keys and config.

ADR-096 to ADR-102 change the agent: 900 s window bounds, pool support and the latency echo.
The installer refuses to run over an existing install, so this tool replaces only the agent's
Python files and installs the probe unit. It never touches keys, AWG configs, slots.json,
agent.json, the credential, the journal or the API host.

Dry run by default: prints the plan. With ``--execute`` it uploads the files of ``--source-rev`` (a
commit, never the working tree) and runs one fixed program on the node. Before anything changes
it verifies that every replaced file still matches ``--baseline-rev`` (default ddf1320, the
current beta) and aborts untouched on any drift. Old files are backed up and restored on any
failure.

Restarting the agent revokes the node's current connections (ADR-039 shutdown). Clients
reconnect by themselves (ADR-099). Run it when few people are connected.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

from scripts.beta_nodes import BetaNode, InventoryError, load_inventory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "infra" / "beta" / "nodes.json"
STEP = re.compile(r"^(step|failed_step|note)=([a-z0-9_]{1,64})$")

# Node path below /opt/detourvpn-node -> repository path.
REPLACED = {
    "window_daemon.py": "services/node-agent/window_daemon.py",
    "window_gate.py": "services/node-agent/window_gate.py",
    "window_peer.py": "services/node-agent/window_peer.py",
    "beta_slot_bootstrap.py": "services/node-agent/beta_slot_bootstrap.py",
    "vpn_api/tunnels/v3_contracts.py": "services/api/src/vpn_api/tunnels/v3_contracts.py",
    "vpn_api/tunnels/window_runtime.py": "services/api/src/vpn_api/tunnels/window_runtime.py",
}
ADDED = {
    "window_pool.py": "services/node-agent/window_pool.py",
    "latency_probe.py": "services/node-agent/latency_probe.py",
}
PROBE_UNIT = "infra/beta-node/detourvpn-beta-latency-probe.service"


class UpdateError(RuntimeError):
    """Fixed codes only; no addresses, keys or raw remote output."""


def git_blob(rev: str, path: str) -> bytes:
    try:
        return subprocess.run(  # noqa: S603, S607 - fixed git verb, repository-relative path
            ["git", "show", f"{rev}:{path}"],  # noqa: S607
            check=True,
            capture_output=True,
            cwd=ROOT,
        ).stdout.replace(b"\r\n", b"\n")
    except subprocess.CalledProcessError:
        raise UpdateError("update_source_missing") from None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


REMOTE = r"""
set -Eeuo pipefail
umask 077
rid="$1"; with_probe="$2"
[[ "$rid" =~ ^[0-9a-f]{32}$ ]] || exit 2
stage="/root/detourvpn-agent-update-$rid"; backup="/root/detourvpn-agent-backup-$rid"
root=/opt/detourvpn-node; unit=detourvpn-beta-node-agent.service
step=checks; changed=0; was_active=0
emit() { step="$1"; printf 'step=%s\n' "$1"; }
restore() {
  while read -r want rel; do
    [ -z "$rel" ] && continue
    if [ -f "$backup/$(echo "$rel" | tr / _)" ]; then cp -a "$backup/$(echo "$rel" | tr / _)" "$root/$rel"; fi
  done < "$stage/replaced"
  while read -r want rel; do [ -z "$rel" ] && continue; rm -f -- "$root/$rel"; done < "$stage/added.new"
  if [ "$was_active" = 1 ]; then systemctl restart "$unit" || true; fi
}
on_error() {
  trap - ERR
  printf 'failed_step=%s\n' "$step"
  if [ "$changed" = 1 ]; then restore; printf 'note=code_restored\n'; fi
  rm -rf -- "$stage"
  exit 1
}
trap on_error ERR

emit checks
test "$(id -u)" = 0
test -d "$root" && test -d "$root/vpn_api/tunnels" && test -x "$root/.venv/bin/python"
test -f "/etc/systemd/system/$unit"
test -f /etc/detourvpn-node/slots.json
test ! -e "$backup"
test "$(df -Pk / | awk 'NR==2{print $4}')" -gt 200000

emit baseline
# Every replaced file must still be the reviewed baseline; any drift aborts untouched.
while read -r want rel; do
  [ -z "$rel" ] && continue
  test "$(sed 's/\r$//' "$root/$rel" | sha256sum | cut -d ' ' -f1)" = "$want"
done < "$stage/baseline"
: > "$stage/added.new"
while read -r want rel; do
  [ -z "$rel" ] && continue
  if [ -e "$root/$rel" ]; then
    test "$(sed 's/\r$//' "$root/$rel" | sha256sum | cut -d ' ' -f1)" = "$want"   # re-run: already this version
  else
    printf '%s %s\n' "$want" "$rel" >> "$stage/added.new"
  fi
done < "$stage/added"
while read -r want name; do
  [ -z "$name" ] && continue
  test "$(sha256sum "$stage/files/$name" | cut -d ' ' -f1)" = "$want"
done < "$stage/uploaded"

emit backup
install -d -m 0700 "$backup"
while read -r want rel; do [ -z "$rel" ] && continue; cp -a "$root/$rel" "$backup/$(echo "$rel" | tr / _)"; done < "$stage/replaced"

emit replace
if systemctl is-active --quiet "$unit"; then was_active=1; fi
changed=1
while read -r want rel; do
  [ -z "$rel" ] && continue
  name="$(echo "$rel" | tr / _)"
  install -m 0600 -o root -g root "$stage/files/$name" "$root/$rel.new-$rid"
  mv -f -- "$root/$rel.new-$rid" "$root/$rel"
  test "$(sha256sum "$root/$rel" | cut -d ' ' -f1)" = "$want"
done < <(cat "$stage/replaced" "$stage/added")
"$root/.venv/bin/python" -I -c 'import sys; sys.path.insert(0, "/opt/detourvpn-node"); import window_daemon, window_pool, latency_probe, beta_slot_bootstrap'

emit restart
if [ "$was_active" = 1 ]; then
  systemctl restart "$unit"
  for i in $(seq 1 20); do systemctl is-active --quiet "$unit" && break; sleep 1; done
  systemctl is-active --quiet "$unit"
  sleep 5
  systemctl is-active --quiet "$unit"
else
  printf 'note=agent_was_not_running\n'
fi

if [ "$with_probe" = 1 ]; then
  emit probe
  install -m 0644 -o root -g root "$stage/files/probe.service" /etc/systemd/system/detourvpn-beta-latency-probe.service
  systemctl daemon-reload
  systemctl enable --now detourvpn-beta-latency-probe.service >/dev/null 2>&1
  systemctl is-active --quiet detourvpn-beta-latency-probe.service
fi

changed=0
rm -rf -- "$stage"
emit done
"""


def build(source_rev: str, baseline_rev: str, with_probe: bool) -> bytes:
    replaced = {rel: git_blob(source_rev, repo) for rel, repo in REPLACED.items()}
    added = {rel: git_blob(source_rev, repo) for rel, repo in ADDED.items()}
    baseline = {rel: git_blob(baseline_rev, repo) for rel, repo in REPLACED.items()}
    lists = {
        "replaced": "".join(f"{sha(d)} {rel}\n" for rel, d in replaced.items()),
        "added": "".join(f"{sha(d)} {rel}\n" for rel, d in added.items()),
        "baseline": "".join(f"{sha(d)} {rel}\n" for rel, d in baseline.items()),
    }
    files = {rel.replace("/", "_"): data for rel, data in {**replaced, **added}.items()}
    if with_probe:
        files["probe.service"] = git_blob(source_rev, PROBE_UNIT)
    lists["uploaded"] = "".join(f"{sha(d)} {name}\n" for name, d in files.items())
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        entries = {
            **{f"files/{n}": d for n, d in files.items()},
            **{n: t.encode() for n, t in lists.items()},
        }
        for name, data in sorted(entries.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            info.uid = info.gid = 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def ssh(
    node: BetaNode, remote: list[str], stdin: bytes, timeout: int
) -> subprocess.CompletedProcess[bytes]:
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(node.ssh_key), "-p", str(node.ssh_port),
        f"{node.ssh_user}@{node.host}", *remote,
    ]  # fmt: skip
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, fixed program on stdin
            command, input=stdin, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise UpdateError("update_ssh_unavailable") from None


HASHES = r"""
set -euo pipefail
cd /opt/detourvpn-node
for rel in "$@"; do
  [[ "$rel" =~ ^[a-z0-9_/]+\.py$ ]] || exit 2
  if [ -f "$rel" ]; then printf '%s %s\n' "$(sed 's/\r$//' "$rel" | sha256sum | cut -d ' ' -f1)" "$rel"; else printf 'absent %s\n' "$rel"; fi
done
"""


def check_baseline(node: BetaNode, baseline_rev: str) -> dict[str, str]:
    """Read-only: which agent files on the node equal the baseline revision (names only)."""
    result = ssh(node, ["bash", "-s", "--", *REPLACED, *ADDED], HASHES.encode(), 60)
    if result.returncode != 0:
        raise UpdateError("update_baseline_read_failed")
    observed = dict(
        line.split(" ", 1)[::-1]
        for line in result.stdout.decode("ascii", "replace").splitlines()
        if " " in line
    )
    report = {}
    for rel, repo in REPLACED.items():
        report[rel] = (
            "match" if observed.get(rel) == sha(git_blob(baseline_rev, repo)) else "differs"
        )
    for rel in ADDED:
        report[rel] = "absent" if observed.get(rel) == "absent" else "present"
    return report


def execute(node: BetaNode, archive: bytes, with_probe: bool) -> list[str]:
    run_id = uuid.uuid4().hex
    stage = f"/root/detourvpn-agent-update-{run_id}"
    upload = ssh(
        node, [f"umask 077 && mkdir {stage} && tar -x -C {stage} --no-same-owner"], archive, 120
    )
    if upload.returncode != 0:
        ssh(node, [f"rm -rf -- {stage}"], b"", 30)
        raise UpdateError("update_upload_failed")
    result = ssh(
        node, ["bash", "-s", "--", run_id, "1" if with_probe else "0"], REMOTE.encode(), 300
    )
    steps = [
        line.strip()
        for line in result.stdout.decode("utf-8", "replace").splitlines()
        if STEP.match(line.strip())
    ]
    if result.returncode != 0 or steps[-1:] != ["step=done"]:
        failed = next((s for s in steps if s.startswith("failed_step=")), "failed_step=unknown")
        restored = "note=code_restored" in steps
        raise UpdateError(
            f"update_failed:{failed.split('=', 1)[1]}" + (":restored" if restored else "")
        )
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, help="node name from the inventory")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--source-rev", default="HEAD", help="committed revision to deploy")
    parser.add_argument("--baseline-rev", default="ddf1320", help="revision the node runs now")
    parser.add_argument("--no-probe", action="store_true", help="skip the ADR-102 latency echo")
    parser.add_argument("--execute", action="store_true", help="really update (default: dry run)")
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="read-only: compare node files to the baseline",
    )
    arguments = parser.parse_args(argv)
    try:
        node = load_inventory(arguments.inventory).get(arguments.node)
        if node is None:
            raise UpdateError("update_node_not_in_inventory")
        if arguments.check_baseline:
            print(json.dumps(check_baseline(node, arguments.baseline_rev), indent=2))
            return 0
        archive = build(arguments.source_rev, arguments.baseline_rev, not arguments.no_probe)
        report: dict[str, object] = {
            "node": node.name,
            "mode": "execute" if arguments.execute else "dry_run",
            "replaced_files": len(REPLACED),
            "added_files": len(ADDED),
            "probe": not arguments.no_probe,
            "archive_bytes": len(archive),
        }
        if arguments.execute:
            report["steps"] = execute(node, archive, not arguments.no_probe)
            report["note"] = (
                "agent restarted; check the node in the admin panel and connect a phone"
            )
    except (InventoryError, UpdateError) as error:
        print(json.dumps({"updated": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

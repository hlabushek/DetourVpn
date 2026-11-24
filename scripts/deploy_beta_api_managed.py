"""Deploy the API code to the beta host, for the managed database (runbook step 3).

Runbook: docs/runbooks/beta-backend-rollout-neon.md. Run it only after migrations 0028 and 0029
are applied to the managed database (step 2).

Dry run by default: it prints the plan and changes nothing. With --execute it:
  1. uploads the API source and migrations of --source-rev (a commit, never the working tree);
  2. backs up the current files on the host into /opt/detourvpn-beta-backup/<time>/;
  3. replaces the files and restarts detourvpn-beta-api;
  4. checks that the service is active and the health endpoint answers;
  5. on any failure restores the backup and restarts the old code.

It never touches the database, api.env, keys or the nodes. Passwords and keys are never printed.
Requires --confirm-migrations-applied, so it cannot run before step 2.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE_DIR = "/opt/detourvpn-beta"
UNIT = "detourvpn-beta-api"
PATHS = ["services/api/src", "services/api/migrations"]
HEALTH = "http://127.0.0.1:28000/api/v1/health"
DEPS = ["services/api/pyproject.toml", "uv.lock", "services/api/uv.lock"]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kw)  # noqa: S603


def git(*args: str) -> str:
    out = run(["git", *args], cwd=ROOT)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed")
    return out.stdout


def ssh(host: str, identity: str, command: str) -> subprocess.CompletedProcess:
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-i", identity,
                f"root@{host}", command])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True, help="beta API address, IP or name")
    p.add_argument("--identity", required=True, help="path to the beta SSH key")
    p.add_argument("--source-rev", required=True, help="committed revision to deploy")
    p.add_argument("--baseline-rev", required=True, help="revision the host runs now")
    p.add_argument("--confirm-migrations-applied", action="store_true")
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()

    src = git("rev-parse", "--verify", a.source_rev).strip()
    base = git("rev-parse", "--verify", a.baseline_rev).strip()
    changed = [line for line in git("diff", "--name-only", base, src, "--", *PATHS).splitlines() if line]
    deps = [line for line in git("diff", "--name-only", base, src, "--", *DEPS).splitlines() if line]
    print(f"source={src[:12]} baseline={base[:12]} files_changed={len(changed)}")
    for f in changed:
        print(f"  {f}")
    if deps:
        print("refused: dependency files changed, the venv would need a sync. Handle that by hand first:")
        for f in deps:
            print(f"  {f}")
        return 2
    if not a.confirm_migrations_applied:
        print("refused: step 2 (migrations 0028 and 0029) is not confirmed. Pass --confirm-migrations-applied.")
        return 2
    if not a.execute:
        print("mode=preflight_only; run again with --execute to deploy")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = f"/opt/detourvpn-beta-backup/{stamp}"
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "api.tar"
        with archive.open("wb") as fh:
            fh.write(subprocess.run(  # noqa: S603
                ["git", "archive", "--format=tar", src, *PATHS], cwd=ROOT, check=True, capture_output=True
            ).stdout)
        up = run(["scp", "-o", "BatchMode=yes", "-i", a.identity, str(archive), f"root@{a.host}:/tmp/api-{stamp}.tar"])
        if up.returncode != 0:
            print("failed: upload")
            return 1

        steps = [
            f"mkdir -p {backup} && cd {REMOTE_DIR} && tar -cf {backup}/before.tar services/api/src services/api/migrations",
            f"cd {REMOTE_DIR} && rm -rf services/api/src services/api/migrations && tar -xf /tmp/api-{stamp}.tar && chown -R detourvpn-beta:detourvpn-beta services/api",
            f"systemctl restart {UNIT}",
        ]
        for cmd in steps:
            r = ssh(a.host, a.identity, cmd)
            if r.returncode != 0:
                print(f"failed: {shlex.quote(cmd.split('&&')[0])[:60]}")
                rollback(a, backup)
                return 1

        time.sleep(5)
        active = ssh(a.host, a.identity, f"systemctl is-active {UNIT}")
        health = ssh(a.host, a.identity, f"curl -fsS --max-time 10 {HEALTH} >/dev/null && echo ok")
        if active.stdout.strip() != "active" or health.stdout.strip() != "ok":
            print("failed: service or health check")
            rollback(a, backup)
            return 1
    print(f"deployed. backup={backup} (keep it until the next deploy)")
    return 0


def rollback(a: argparse.Namespace, backup: str) -> None:
    print("rolling back to the backup")
    ssh(a.host, a.identity,
        f"cd {REMOTE_DIR} && rm -rf services/api/src services/api/migrations && tar -xf {backup}/before.tar && "
        f"systemctl restart {UNIT}")


if __name__ == "__main__":
    sys.exit(main())

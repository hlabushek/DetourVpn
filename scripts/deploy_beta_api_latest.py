# ruff: noqa: E501, S607
"""Deploy ALL API code and migrations since the baseline (0023..latest) to the isolated beta host.

Generalised from deploy_beta_api_0025.py: the changed/new file lists and the target revision are
computed from git (baseline-rev..source-rev), so migrations 0026/0027 and new modules are included.
Same safety: baseline hashes, DB dump, automatic rollback. Preflight only unless --execute.
Not run against a server by its author.

Covers ADR-096 (long windows), ADR-097 (native renew token), ADR-100 (refresh replay grace and
heartbeat tolerance), ADR-101 (pool nodes) and the link-code redeem fix. Modelled on
deploy_beta_windows_0022.py. Preflight only unless --execute is given. Uses only the approved
isolated-beta identity and never prints secrets.

Sources come from git blobs of ``--source-rev`` (a commit the owner made; never the working tree).
Baselines are the blobs of ``--baseline-rev`` (default ddf1320, the 0022 deploy); they are checked
on the host before anything changes, so a host that drifted aborts the run untouched. On failure the
old code is restored. All three migrations are additive (nullable columns, a wider check), so the
old code keeps working if they stay applied.

Deploy order (ADR-096): every node agent first, then this, then the client.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HOST = "193.68.88.239"
EXPECTED_HOST = "hiplet-137728"
IDENTITY = Path(os.environ["USERPROFILE"]) / ".ssh" / "detourvpn-beta-20261026"
API = "services/api/src/vpn_api"
MIGRATIONS = "services/api/migrations/versions"
BEFORE = "0022_device_platform_windows"
SCOPE = ("services/api/src/vpn_api", "services/api/migrations/versions")


def changed_files(baseline: str, source: str) -> tuple[list[str], list[str]]:
    out = subprocess.run(  # noqa: S603, S607
        ["git", "diff", "--name-status", baseline, source, "--", *SCOPE],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    modified, added = [], []
    for line in out.splitlines():
        status, _, path = line.partition(chr(9))
        if "__pycache__" in path or not path.endswith(".py"):
            continue
        if status == "M":
            modified.append(path)
        elif status == "A":
            added.append(path)
        else:
            raise SystemExit(f"unsupported_change status={status} path={path}")
    return sorted(modified), sorted(added)


def target_revision(source: str, added: list[str], before: str) -> str:
    migrations = sorted(p for p in added if "/migrations/versions/" in p)
    if not migrations:
        return before
    text = git_blob(source, migrations[-1]).decode()
    match = re.search(r'(?m)^revision\s*(?::\s*str\s*)?=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("revision_not_found")
    return match.group(1)


def git_blob(rev: str, path: str) -> bytes:
    return subprocess.run(  # noqa: S603, S607
        ["git", "show", f"{rev}:{path}"], check=True, capture_output=True
    ).stdout.replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SCRIPT = r"""set -Eeuo pipefail
set +x
rid="$1"; stage="/run/detourvpn-dev0022-$rid"; backup="/root/detourvpn-pre-0025-$rid"
root=/opt/detourvpn-beta; changed=0; phase=preflight
q() { docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"'; }
ready() { curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true; }
cleanup_stage() {
  rm -f -- /run/detourvpn-dev0022-migrate-"$rid".env
  rm -f -- /run/detourvpn-dev0022-"$rid"/*
  rmdir -- /run/detourvpn-dev0022-"$rid" 2>/dev/null || true
}
restore_sources() {
  while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    cp -a "$backup/source/$(echo "$rel" | tr / _)" "$root/$rel"
  done <<'LIST'
__CHANGED__
LIST
  # The new migration files are removed only while the schema is still at the old revision.
  if [ "$(q)" = __BEFORE__ ]; then
    for name in __NEW_NAMES__; do rm -f -- "/opt/detourvpn-beta/services/api/migrations/versions/$name"; done
  fi
  while IFS= read -r rel; do [ -z "$rel" ] && continue; case "$rel" in */migrations/versions/*) ;; *) rm -f -- "$root/$rel" ;; esac; done <<'LIST'
__NEWFILES__
LIST
}
rollback() {
  rc=$?; echo rollback_phase="$phase"
  if [ "$changed" = 1 ]; then
    systemctl stop detourvpn-beta-api.service || true
    restore_sources || true
    echo schema_revision_after_rollback="$(q || echo unknown)"
    systemctl start detourvpn-beta-api.service || true
    for i in $(seq 1 30); do [ "$(ready)" = 200 ] && { echo rollout=code_rolled_back; cleanup_stage; exit "$rc"; }; sleep 1; done
    echo rollout=rollback_readiness_failed; exit 70
  fi
  cleanup_stage; exit "$rc"
}
trap rollback ERR
test "$(hostname -s)" = __EXPECTED_HOST__
# ADR-176: this script's schema backup, rollback and migration act on the LOCAL Docker database
# only. A primary whose API reads the managed database would be migrated and dumped in the wrong
# place, so the run refuses before anything changes.
grep -Eq '^VPN_DATABASE_URL="?postgresql\+psycopg://[^:@/]+:[^@]*@127\.0\.0\.1:25433/detourvpn_beta"?$' /etc/detourvpn-beta/api.env || { echo refused=database_not_local_docker; exit 1; }
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "$(ready)" = 200
test "$(docker inspect --format '{{.State.Health.Status}}' detourvpn-beta-postgres-1)" = healthy
test "$(q)" = __BEFORE__
test "$(df -Pk / | awk 'NR==2{print $4}')" -gt 1000000
test "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)" -gt 350000
phase=verify_baselines
while read -r want rel; do
  [ -z "$rel" ] && continue
  test "$(sed 's/\r$//' "$root/$rel" | sha256sum | cut -d ' ' -f1)" = "$want"
done <<'LIST'
__BASELINES__
LIST
while IFS= read -r rel; do [ -z "$rel" ] && continue; test ! -e "$root/$rel"; done <<'LIST'
__NEWFILES__
LIST
test ! -e "$backup"; test "$(stat -c '%a' /root)" = 700
phase=verify_uploaded
while read -r want name; do [ -z "$name" ] && continue; test "$(sha256sum "$stage/$name" | cut -d ' ' -f1)" = "$want"; done <<'LIST'
__UPLOADED__
LIST
phase=backup
umask 077
install -d -o root -g root -m 0700 "$backup" "$backup/source"
while IFS= read -r rel; do [ -z "$rel" ] && continue; cp -a "$root/$rel" "$backup/source/$(echo "$rel" | tr / _)"; done <<'LIST'
__CHANGED__
LIST
docker exec detourvpn-beta-postgres-1 sh -c 'set -eu; export PGPASSWORD="$(cat /run/secrets/pg_migrator)"; exec pg_dump -Fc -h 127.0.0.1 -U vpn_migrator -d detourvpn_beta' > "$backup/database.dump"
test -s "$backup/database.dump"
docker exec -i detourvpn-beta-postgres-1 pg_restore --list >/dev/null < "$backup/database.dump"
chmod 0600 "$backup/database.dump"
printf 'database_backup_bytes=%s\n' "$(stat -c '%s' "$backup/database.dump")"
printf 'database_backup_sha256=%s\n' "$(sha256sum "$backup/database.dump" | cut -d ' ' -f1)"
python3 - "/run/detourvpn-dev0022-migrate-$rid.env" <<'PY'
from pathlib import Path
from urllib.parse import quote
import os, sys
password = Path('/etc/detourvpn-beta/secrets/pg-migrator').read_text(encoding='utf-8').rstrip('\n')
if not password or '\n' in password or '\r' in password:
    raise SystemExit('migration_secret_file_invalid')
url = 'postgresql+psycopg://vpn_migrator:' + quote(password, safe='') + '@127.0.0.1:25433/detourvpn_beta'
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    stream.write('VPN_DATABASE_URL=' + url + '\n')
PY
changed=1
phase=replace_and_migrate
systemctl stop detourvpn-beta-api.service
while read -r want name; do
  [ -z "$name" ] && continue
  rel="$(awk -v n="$name" '$1==n{print $2}' "$stage/map" | tr -d '\r')"
  case "$rel" in services/api/*) ;; *) echo bad_relative_path; false ;; esac
  test -n "$rel"
  new="$root/$rel.dev0022-$rid"
  if [ -e "$root/$rel" ]; then
    install -o "$(stat -c '%U' "$root/$rel")" -g "$(stat -c '%G' "$root/$rel")" -m "$(stat -c '%a' "$root/$rel")" "$stage/$name" "$new"
  else
    install -o root -g detourvpn-beta -m 0640 "$stage/$name" "$new"
  fi
  mv -f -- "$new" "$root/$rel"
done <<'LIST'
__UPLOADED__
LIST
while read -r want rel; do [ -z "$rel" ] && continue; test "$(sha256sum "$root/$rel" | cut -d ' ' -f1)" = "$want"; done <<'LIST'
__INSTALLED__
LIST
systemd-run --quiet --wait --pipe --collect --unit="detourvpn-dev0022-migrate-$rid" --property=WorkingDirectory="$root" --property="EnvironmentFile=/run/detourvpn-dev0022-migrate-$rid.env" "$root/.venv/bin/python" scripts/closed_migrate.py >"$backup/migrate.out" 2>&1 || echo migrate_unit_failed=1
echo migrate_out="$(tr '\n' ' ' < "$backup/migrate.out" | cut -c1-400)"
echo revision_after_migrate="$(q || echo query_failed)"
test "$(q)" = __AFTER__
rm -f -- /run/detourvpn-dev0022-migrate-"$rid".env
phase=api_startup
systemctl start detourvpn-beta-api.service
for a in $(seq 1 30); do
  if systemctl is-active --quiet detourvpn-beta-api.service && [ "$(ready)" = 200 ]; then break; fi
  [ "$a" -lt 30 ] || { echo rollout=readiness_failed; false; }; sleep 1
done
phase=verify
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/nodes)" = 401
echo rollout=success; echo readiness=200; echo database_revision=__AFTER__
changed=0; trap - ERR; cleanup_stage
"""

WANTED = {
    "rollout", "rollback_phase", "readiness", "database_revision", "database_backup_bytes",
    "database_backup_sha256", "schema_revision_after_rollback", "migrate_unit_failed",
    "migrate_out", "revision_after_migrate",
}  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--before-revision",
        default=BEFORE,
        help="alembic revision the host is at now (default: the pre-overnight 0022)",
    )
    parser.add_argument("--source-rev", default="HEAD", help="committed revision to deploy")
    parser.add_argument("--baseline-rev", default="ddf1320", help="revision the host runs now")
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    CHANGED, NEW = changed_files(args.baseline_rev, args.source_rev)
    AFTER = target_revision(args.source_rev, NEW, args.before_revision)
    MIGS = [p for p in NEW if "/migrations/versions/" in p]
    files = {p: git_blob(args.source_rev, p) for p in CHANGED + NEW}
    names = {p: p.replace("/", "_") for p in files}
    baselines = "\n".join(f"{sha(git_blob(args.baseline_rev, p))} {p}" for p in CHANGED)
    script = (
        SCRIPT.replace("__EXPECTED_HOST__", EXPECTED_HOST)
        .replace("__BEFORE__", args.before_revision)
        .replace("__AFTER__", AFTER)
        .replace("__NEW_NAMES__", " ".join(Path(p).name for p in MIGS))
        .replace("__NEWFILES__", chr(10).join(NEW))
        .replace("__BASELINES__", baselines)
        .replace("__UPLOADED__", "\n".join(f"{sha(files[p])} {names[p]}" for p in files))
        .replace("__INSTALLED__", "\n".join(f"{sha(files[p])} {p}" for p in files))
        .replace("__CHANGED__", "\n".join(CHANGED))
    )
    if not args.execute:
        print(
            f"mode=preflight_only; files={len(files)}; baselines={len(CHANGED)}; target={AFTER}; run with --execute"
        )
        return 0
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY), f"root@{HOST}",
    ]  # fmt: skip
    scp = [
        "scp", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY),
    ]  # fmt: skip
    stage = f"/run/detourvpn-dev0022-{run_id}"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for p, data in files.items():
            (t / names[p]).write_bytes(data)
        # Bytes, not write_text: Windows text mode would add carriage returns to the host's paths.
        (t / "map").write_bytes(("\n".join(f"{names[p]} {p}" for p in files) + "\n").encode())
        (t / "deploy.sh").write_bytes(script.replace("\r", "").encode("utf-8"))
        subprocess.run(  # noqa: S603
            [*ssh, f"set -eu; test ! -e '{stage}'; install -d -o root -g root -m 0700 '{stage}'"],
            check=True,
        )
        for f in t.iterdir():
            for attempt in range(1, 5):  # the link to the host drops now and then
                done = subprocess.run(  # noqa: S603
                    [*scp, str(f), f"root@{HOST}:{stage}/{f.name}"], check=False
                )
                if done.returncode == 0:
                    break
                if attempt == 4:
                    raise SystemExit(f"upload_failed file={f.name}")
    result = subprocess.run(  # noqa: S603
        [*ssh, f"chmod 0700 '{stage}/deploy.sh' && bash '{stage}/deploy.sh' '{run_id}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    print("\n".join(x for x in result.stdout.splitlines() if x.split("=")[0] in WANTED))
    if result.returncode != 0 and result.stderr.strip():
        # The remote shell's own error text was previously discarded; show it so a failure is diagnosable.
        print("remote_stderr_begin", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        print("remote_stderr_end", file=sys.stderr)
    print(f"exit_code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

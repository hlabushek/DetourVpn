# ruff: noqa: E501, S607
"""Deploy migration 0022 and the matching API code to the isolated beta host (I-032, ADR-095).

Modelled on deploy_beta_devices_0021.py. Preflight only unless --execute is given. Uses only the
approved isolated-beta identity and never prints secrets. Source files come from git HEAD blobs (LF).
Baselines are the blobs of the previous revision of the same two files (HEAD of the 0021 deploy, i.e.
the parent of the commit that added 0022); they are checked on the host before anything changes, so a
host that drifted aborts the run untouched. On failure the old code is restored. The schema change is
additive (a wider check constraint), so the old code keeps working if the migration stays applied.

Changes: devices.py and db/models.py (platform Literal) and the new migration 0022. Nothing else.
"""

import argparse
import hashlib
import os
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
CHANGED = [f"{API}/db/models.py", f"{API}/devices.py"]
NEW = [f"{MIGRATIONS}/0022_device_platform_windows.py"]
# The commit that introduced 0022; its parent holds what the host runs after the 0021 deploy.
INTRODUCING_COMMIT = "0a76f9c"


def git_blob(rev: str, path: str) -> bytes:
    return subprocess.run(  # noqa: S603, S607
        ["git", "show", f"{rev}:{path}"], check=True, capture_output=True
    ).stdout.replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SCRIPT = r"""set -Eeuo pipefail
set +x
rid="$1"; stage="/run/detourvpn-dev0022-$rid"; backup="/root/detourvpn-pre-0022-$rid"
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
  # The new migration file is removed only while the schema is still at 0021.
  if [ "$(q)" = 0021_device_link_codes ]; then
    rm -f -- /opt/detourvpn-beta/services/api/migrations/versions/0022_device_platform_windows.py
  fi
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
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "$(ready)" = 200
test "$(docker inspect --format '{{.State.Health.Status}}' detourvpn-beta-postgres-1)" = healthy
test "$(q)" = 0021_device_link_codes
test "$(df -Pk / | awk 'NR==2{print $4}')" -gt 1000000
test "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)" -gt 350000
phase=verify_baselines
while read -r want rel; do
  [ -z "$rel" ] && continue
  test "$(sed 's/\r$//' "$root/$rel" | sha256sum | cut -d ' ' -f1)" = "$want"
done <<'LIST'
__BASELINES__
LIST
test ! -e "$root/services/api/migrations/versions/0022_device_platform_windows.py"
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
test "$(q)" = 0022_device_platform_windows
rm -f -- /run/detourvpn-dev0022-migrate-"$rid".env
phase=api_startup
systemctl start detourvpn-beta-api.service
for a in $(seq 1 30); do
  if systemctl is-active --quiet detourvpn-beta-api.service && [ "$(ready)" = 200 ]; then break; fi
  [ "$a" -lt 30 ] || { echo rollout=readiness_failed; false; }; sleep 1
done
phase=verify
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/nodes)" = 401
echo rollout=success; echo readiness=200; echo database_revision=0022_device_platform_windows
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
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    files = {p: git_blob("HEAD", p) for p in CHANGED + NEW}
    names = {p: p.replace("/", "_") for p in files}
    baselines = "\n".join(f"{sha(git_blob(INTRODUCING_COMMIT + '^', p))} {p}" for p in CHANGED)
    script = (
        SCRIPT.replace("__EXPECTED_HOST__", EXPECTED_HOST)
        .replace("__BASELINES__", baselines)
        .replace("__UPLOADED__", "\n".join(f"{sha(files[p])} {names[p]}" for p in files))
        .replace("__INSTALLED__", "\n".join(f"{sha(files[p])} {p}" for p in files))
        .replace("__CHANGED__", "\n".join(CHANGED))
    )
    if not args.execute:
        print(
            f"mode=preflight_only; files={len(files)}; baselines={len(CHANGED)}; run with --execute"
        )
        return 0
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY), f"root@{HOST}",
    ]  # fmt: skip
    scp = [
        "scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
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
    print(f"exit_code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

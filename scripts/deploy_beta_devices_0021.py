"""Deploy migrations 0020/0021 and the matching API code to the isolated beta host (ADR-092/093).

Modelled on deploy_i030_unknown_resolution.ps1. Preflight only unless --execute is given.
Uses only the approved isolated-beta identity; never prints secrets. Source files come from
git HEAD blobs (LF); baselines (CRLF-normalised sha256 of what is on the host) are read from the
file named in BASELINE_FILE and are checked on the host before anything is changed. On failure the
old code is restored (the additive schema is left in place, see the runbook).
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
ROOT = "/opt/detourvpn-beta"
API = "services/api/src/vpn_api"
MIGRATIONS = "services/api/migrations/versions"

CHANGED = [
    f"{API}/account.py",
    f"{API}/auth/contracts.py",
    f"{API}/auth/routes.py",
    f"{API}/auth/security.py",
    f"{API}/auth/service.py",
    f"{API}/config.py",
    f"{API}/db/models.py",
    f"{API}/devices.py",
    f"{API}/main.py",
    f"{API}/nodes/catalog.py",
    f"{API}/nodes/contracts.py",
    f"{API}/nodes/service.py",
    f"{API}/tunnels/window_routes.py",
    f"{API}/tunnels/window_service.py",
]
NEW = [
    f"{API}/devices_usage.py",
    f"{MIGRATIONS}/0020_vpn_node_country.py",
    f"{MIGRATIONS}/0021_device_link_codes.py",
]


def blob(path: str) -> bytes:
    return subprocess.run(  # noqa: S603, S607
        ["git", "show", "HEAD:" + path], check=True, capture_output=True
    ).stdout.replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Removals use literal absolute prefixes so a variable can never expand to a root path.
SCRIPT = r"""set -Eeuo pipefail
set +x
rid="$1"; stage="/run/detourvpn-dev0021-$rid"; backup="/root/detourvpn-pre-0020-$rid"
root=/opt/detourvpn-beta; changed=0; phase=preflight
q() { docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"'; }
ready() { curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true; }
cleanup_stage() {
  rm -f -- /run/detourvpn-dev0021-migrate-"$rid".env
  rm -f -- /run/detourvpn-dev0021-"$rid"/*
  rmdir -- /run/detourvpn-dev0021-"$rid" 2>/dev/null || true
}
restore_sources() {
  while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    cp -a "$backup/source/$(echo "$rel" | tr / _)" "$root/$rel"
  done <<'LIST'
__CHANGED__
LIST
  while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    case "$rel" in
      *migrations*) [ "$(q)" = 0019_account_deletion ] && rm -f -- /opt/detourvpn-beta/"$rel" ;;
      *) rm -f -- /opt/detourvpn-beta/"$rel" ;;
    esac
  done <<'LIST'
__NEW__
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
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "$(ready)" = 200
test "$(docker inspect --format '{{.State.Health.Status}}' detourvpn-beta-postgres-1)" = healthy
test "$(q)" = 0019_account_deletion
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
__NEW__
LIST
test "$(sha256sum "$root/scripts/closed_migrate.py" | cut -d ' ' -f1)" = __MIGRATOR__
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
python3 - "/run/detourvpn-dev0021-migrate-$rid.env" <<'PY'
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
  rel="$(awk -v n="$name" '$1==n{print $2}' "$stage/map")"
  new="$root/$rel.dev0021-$rid"
  if [ -e "$root/$rel" ]; then
    install -o "$(stat -c '%U' "$root/$rel")" -g "$(stat -c '%G' "$root/$rel")" -m "$(stat -c '%a' "$root/$rel")" "$stage/$name" "$new"
  else
    install -o root -g detourvpn-beta -m 0640 "$stage/$name" "$new"
  fi
  mv -f -- "$new" "$root/$rel"
done <<'LIST'
__UPLOADED__
LIST
systemd-run --quiet --wait --pipe --collect --unit="detourvpn-dev0021-migrate-$rid" --property=WorkingDirectory="$root" --property="EnvironmentFile=/run/detourvpn-dev0021-migrate-$rid.env" "$root/.venv/bin/python" scripts/closed_migrate.py >/dev/null
test "$(q)" = 0021_device_link_codes
rm -f -- /run/detourvpn-dev0021-migrate-"$rid".env
phase=api_startup
systemctl start detourvpn-beta-api.service
for a in $(seq 1 30); do
  if systemctl is-active --quiet detourvpn-beta-api.service && [ "$(ready)" = 200 ]; then break; fi
  [ "$a" -lt 30 ] || { echo rollout=readiness_failed; false; }; sleep 1
done
phase=verify
while read -r want rel; do [ -z "$rel" ] && continue; test "$(sha256sum "$root/$rel" | cut -d ' ' -f1)" = "$want"; done <<'LIST'
__INSTALLED__
LIST
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/nodes)" = 401
code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data '{"code":"ZZZZ-ZZZZ"}' http://127.0.0.1:28000/api/v1/auth/link/redeem)"
case "$code" in 4??) ;; *) echo redeem_probe_unexpected="$code"; false ;; esac
echo redeem_probe_http="$code"
echo rollout=success; echo readiness=200; echo database_revision=0021_device_link_codes
changed=0; trap - ERR; cleanup_stage
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    base: dict[str, str] = {}
    for line in Path(os.environ["BASELINE_FILE"]).read_text(encoding="utf-8").splitlines():
        digest, path = line.split("  ", 1)
        base[path] = digest
    for path in CHANGED + ["scripts/closed_migrate.py"]:
        assert path in base, path
    files = {p: blob(p) for p in CHANGED + NEW}
    names = {p: p.replace("/", "_") for p in files}
    uploaded = "\n".join(f"{sha(files[p])} {names[p]}" for p in files)
    installed = "\n".join(f"{sha(files[p])} {p}" for p in files)
    baselines = "\n".join(f"{base[p]} {p}" for p in CHANGED)
    script = (
        SCRIPT.replace("__EXPECTED_HOST__", EXPECTED_HOST)
        .replace("__MIGRATOR__", base["scripts/closed_migrate.py"])
        .replace("__BASELINES__", baselines)
        .replace("__UPLOADED__", uploaded)
        .replace("__INSTALLED__", installed)
        .replace("__CHANGED__", "\n".join(CHANGED))
        .replace("__NEW__", "\n".join(NEW))
    )
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY), f"root@{HOST}",
    ]
    scp = [
        "scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
        "-i", str(IDENTITY),
    ]
    stage = f"/run/detourvpn-dev0021-{run_id}"
    if not args.execute:
        print(f"mode=preflight_only; files={len(files)}; run with --execute")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for p, data in files.items():
            (t / names[p]).write_bytes(data)
        (t / "map").write_text("\n".join(f"{names[p]} {p}" for p in files) + "\n", encoding="utf-8")
        (t / "deploy.sh").write_bytes(script.replace("\r", "").encode("utf-8"))
        cmd = f"set -eu; test ! -e '{stage}'; install -d -o root -g root -m 0700 '{stage}'"
        subprocess.run(ssh + [cmd], check=True)  # noqa: S603
        for f in t.iterdir():
            subprocess.run(scp + [str(f), f"root@{HOST}:{stage}/{f.name}"], check=True)  # noqa: S603
    result = subprocess.run(  # noqa: S603
        ssh + [f"chmod 0700 '{stage}/deploy.sh' && bash '{stage}/deploy.sh' '{run_id}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    wanted = {
        "rollout", "rollback_phase", "readiness", "database_revision", "database_backup_bytes",
        "database_backup_sha256", "redeem_probe_http", "schema_revision_after_rollback",
    }
    print("\n".join(x for x in result.stdout.splitlines() if x.split("=")[0] in wanted))
    print(f"exit_code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

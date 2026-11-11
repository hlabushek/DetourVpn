# ruff: noqa: E501
"""Publish a release APK and its signed manifest on the beta API host (ADR-107).

What --execute does on the host (dry run by default; nothing is changed without it):
  1. uploads the APK and app-update.json to /var/lib/detourvpn-beta/app-update (root:detourvpn-beta);
  2. sets VPN_APP_UPDATE_MANIFEST_FILE and VPN_APP_UPDATE_DOWNLOAD_DIR in the API environment file
     (the old file is backed up) and restarts the beta API;
  3. adds ONE line to the live Caddyfile so /api/v1/app/* reaches the API (backup, validate,
     reload; the old file is restored if anything fails). The live file is patched, not replaced:
     it may hold node entries that the repository template does not.
  4. checks that the manifest and the APK are served locally and through Caddy.
Everything is rolled back automatically on failure. Needs the API code with the download route
(scripts/deploy_beta_api_latest.py) to be deployed first. The app downloads from
https://<host>/api/v1/app/download/<apk file name>.

    uv run --frozen python scripts/publish_beta_app_update.py --apk <apk> --manifest <app-update.json>
"""

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.deploy_beta_api_latest import EXPECTED_HOST, HOST, IDENTITY  # noqa: E402

APK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.apk")
SSH_OPTIONS = [
    "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=yes",
    "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY),
]  # fmt: skip

SCRIPT = r"""set -Eeuo pipefail
set +x
rid="$1"; apk="$2"; host_ip="$3"
stage="/run/detourvpn-appupd-$rid"
dir=/var/lib/detourvpn-beta/app-update
envf=/etc/detourvpn-beta/api.env
caddyf=/etc/caddy/Caddyfile
backup="/root/detourvpn-appupd-$rid"
phase=preflight; env_changed=0; caddy_changed=0
ready() { curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true; }
wait_ready() { for i in $(seq 1 30); do [ "$(ready)" = 200 ] && return 0; sleep 1; done; return 1; }
rollback() {
  rc=$?; echo rollback_phase="$phase"
  if [ "$caddy_changed" = 1 ]; then cp -a "$backup/Caddyfile" "$caddyf" && systemctl reload caddy || true; fi
  if [ "$env_changed" = 1 ]; then
    cp -a "$backup/api.env" "$envf"; systemctl restart detourvpn-beta-api.service || true
    wait_ready && echo rollout=rolled_back || echo rollout=rollback_readiness_failed
  fi
  rm -rf -- "$stage"; exit "$rc"
}
trap rollback ERR
test "$(hostname -s)" = __EXPECTED_HOST__
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet caddy
test "$(ready)" = 200
test "$(df -Pk / | awk 'NR==2{print $4}')" -gt 500000
test -f "$envf"; test -f "$caddyf"; test ! -e "$backup"
test "$(sha256sum "$stage/$apk" | cut -d ' ' -f1)" = __APK_SHA__
test "$(sha256sum "$stage/app-update.json" | cut -d ' ' -f1)" = __MANIFEST_SHA__
phase=backup
umask 077
install -d -o root -g root -m 0700 "$backup"
cp -a "$envf" "$backup/api.env"; cp -a "$caddyf" "$backup/Caddyfile"
phase=files
install -d -o root -g detourvpn-beta -m 0750 "$dir"
install -o root -g detourvpn-beta -m 0640 "$stage/$apk" "$dir/$apk.new"
install -o root -g detourvpn-beta -m 0640 "$stage/app-update.json" "$dir/app-update.json.new"
mv -f -- "$dir/$apk.new" "$dir/$apk"; mv -f -- "$dir/app-update.json.new" "$dir/app-update.json"
test "$(sha256sum "$dir/$apk" | cut -d ' ' -f1)" = __APK_SHA__
phase=api_env
env_changed=1
python3 - "$envf" "$dir" <<'PY'
import os, sys
path, folder = sys.argv[1], sys.argv[2]
keep = [x for x in open(path, encoding="utf-8").read().splitlines()
        if not x.startswith(("VPN_APP_UPDATE_MANIFEST_FILE=", "VPN_APP_UPDATE_DOWNLOAD_DIR="))]
keep += ["VPN_APP_UPDATE_MANIFEST_FILE=" + folder + "/app-update.json",
         "VPN_APP_UPDATE_DOWNLOAD_DIR=" + folder]
tmp = path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
with os.fdopen(fd, "w", encoding="utf-8") as out:
    out.write("\n".join(keep) + "\n")
st = os.stat(path)
os.chown(tmp, st.st_uid, st.st_gid); os.chmod(tmp, st.st_mode & 0o777)
os.replace(tmp, path)
PY
systemctl restart detourvpn-beta-api.service
wait_ready || { echo rollout=api_readiness_failed; false; }
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/app/signed-update-manifest)" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/app/download/$apk)" = 200
phase=caddy
python3 - "$caddyf" "$backup/Caddyfile.new" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
if "/api/v1/app/*" in text:
    open(dst, "w", encoding="utf-8").write(text); raise SystemExit(0)
anchor = "\t\tpath /api/v1/nodes /api/v1/quota /api/v1/quota/*\n"
if text.count(anchor) != 1:
    raise SystemExit("caddy_anchor_not_unique")
open(dst, "w", encoding="utf-8").write(text.replace(anchor, anchor + "\t\tpath /api/v1/app/*\n"))
PY
/usr/bin/caddy validate --config "$backup/Caddyfile.new" --adapter caddyfile
install -o root -g caddy -m 0640 "$backup/Caddyfile.new" "$caddyf.candidate-$rid"
mv -f -- "$caddyf.candidate-$rid" "$caddyf"
caddy_changed=1
systemctl reload caddy; systemctl is-active --quiet caddy
phase=verify_public
test "$(curl -sk -o /dev/null -w '%{http_code}' "https://$host_ip/api/v1/app/signed-update-manifest")" = 200
test "$(curl -sk -o /dev/null -w '%{http_code}' "https://$host_ip/api/v1/app/download/$apk")" = 200
echo rollout=success
echo apk_url=https://$host_ip/api/v1/app/download/$apk
caddy_changed=0; env_changed=0; trap - ERR; rm -rf -- "$stage"
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.apk.is_file() or not args.manifest.is_file():
        raise SystemExit("apk_or_manifest_missing")
    if not APK_NAME.fullmatch(args.apk.name):
        raise SystemExit("apk_file_name_must_be_plain, for example DetourVpn-0.1.0-beta.27.apk")
    apk_sha, manifest_sha = sha(args.apk), sha(args.manifest)
    print(f"apk={args.apk.name} bytes={args.apk.stat().st_size} sha256={apk_sha[:16]}…")
    print(f"manifest sha256={manifest_sha[:16]}…")
    print(f"download_url=https://<host>/api/v1/app/download/{args.apk.name}")
    if not args.execute:
        print("mode=preflight_only; run with --execute")
        return 0
    rid = uuid.uuid4().hex
    script = (
        SCRIPT.replace("__EXPECTED_HOST__", EXPECTED_HOST)
        .replace("__APK_SHA__", apk_sha)
        .replace("__MANIFEST_SHA__", manifest_sha)
    )
    stage = f"/run/detourvpn-appupd-{rid}"
    ssh = ["ssh", *SSH_OPTIONS, f"root@{HOST}"]
    scp = ["scp", *SSH_OPTIONS]
    subprocess.run(  # noqa: S603
        [*ssh, f"set -eu; test ! -e '{stage}'; install -d -o root -g root -m 0700 '{stage}'"],
        check=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        deploy = Path(tmp) / "deploy.sh"
        deploy.write_bytes(script.replace("\r", "").encode("utf-8"))
        uploads = [
            (args.apk, args.apk.name),
            (args.manifest, "app-update.json"),
            (deploy, "deploy.sh"),
        ]
        for source, name in uploads:
            for attempt in range(1, 5):  # the link to the host drops now and then
                done = subprocess.run(  # noqa: S603
                    [*scp, str(source), f"root@{HOST}:{stage}/{name}"], check=False
                )
                if done.returncode == 0:
                    break
                if attempt == 4:
                    raise SystemExit(f"upload_failed file={name}")
    result = subprocess.run(  # noqa: S603
        [
            *ssh,
            f"chmod 0700 '{stage}/deploy.sh' && bash '{stage}/deploy.sh' '{rid}' '{args.apk.name}' '{HOST}'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    wanted = ("rollout", "rollback_phase", "apk_url")
    print("\n".join(x for x in result.stdout.splitlines() if x.split("=")[0] in wanted))
    print(f"exit_code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

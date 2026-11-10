"""Publish a built Telegram Mini App as a new immutable release on the isolated beta host.

Builds nothing: run `npm run build` in apps/telegram-mini-app first. The release directory is
named after the content hash, `VPN_MINI_APP_STATIC_DIR` in api.env is switched to it (the old
release stays on disk), and the API is restarted. On failure api.env is restored and the API
restarted. Uses only the approved isolated-beta identity; prints no secrets.
Preflight only unless --execute is given. All files go over the wire as bytes (LF only).
"""

import argparse
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

HOST = "193.68.88.239"
EXPECTED_HOST = "hiplet-137728"
IDENTITY = Path(os.environ["USERPROFILE"]) / ".ssh" / "detourvpn-beta-20261026"
DIST = Path(__file__).resolve().parents[1] / "apps" / "telegram-mini-app" / "dist"
CURRENT_RELEASE = "a55e2128caf6499a88d2d36777bad260"

# Removals only ever use a literal absolute prefix.
SCRIPT = r"""set -Eeuo pipefail
set +x
rid="$1"; release="$2"
stage=/run/detourvpn-mini-"$rid"; releases=/opt/detourvpn-beta/mini-app/releases
envfile=/etc/detourvpn-beta/api.env; envbak=/root/detourvpn-api.env.pre-mini-"$rid"
changed=0; phase=preflight
ready() { curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true; }
cleanup_stage() { rm -f -- /run/detourvpn-mini-"$rid"/*; rmdir -- /run/detourvpn-mini-"$rid" 2>/dev/null || true; }
rollback() {
  rc=$?; echo rollback_phase="$phase"
  if [ "$changed" = 1 ]; then
    cp -a "$envbak" "$envfile" || true
    systemctl restart detourvpn-beta-api.service || true
    for i in $(seq 1 30); do [ "$(ready)" = 200 ] && { echo rollout=env_rolled_back; cleanup_stage; exit "$rc"; }; sleep 1; done
    echo rollout=rollback_readiness_failed; exit 70
  fi
  cleanup_stage; exit "$rc"
}
trap rollback ERR
test "$(hostname -s)" = __EXPECTED_HOST__
systemctl is-active --quiet detourvpn-beta-api.service
test "$(ready)" = 200
test "$(grep -c '^VPN_MINI_APP_STATIC_DIR=' "$envfile")" = 1
# The current release is whatever the host serves now (a previous deploy moved it); it must be a
# content-hash directory under $releases that exists. The old one is kept for rollback.
current_line="$(grep '^VPN_MINI_APP_STATIC_DIR=' "$envfile")"
[[ "$current_line" =~ ^VPN_MINI_APP_STATIC_DIR=$releases/[0-9a-f]{32}$ ]]
test -d "${current_line#VPN_MINI_APP_STATIC_DIR=}"
test ! -e "$releases/$release"; test ! -e "$releases/.incoming-$rid"; test ! -e "$envbak"
test "$(sha256sum "$stage/release.tar.gz" | cut -d ' ' -f1)" = __TARSHA__
phase=extract
install -d -o root -g detourvpn-beta -m 0750 "$releases/.incoming-$rid"
tar -xzf "$stage/release.tar.gz" -C "$releases/.incoming-$rid" --no-same-owner --no-same-permissions
chown -R root:detourvpn-beta "$releases/.incoming-$rid"
find "$releases/.incoming-$rid" -type d -exec chmod 0750 {} +
find "$releases/.incoming-$rid" -type f -exec chmod 0640 {} +
phase=verify_release
while read -r want rel; do
  [ -z "$rel" ] && continue
  test "$(sha256sum "$releases/.incoming-$rid/$rel" | cut -d ' ' -f1)" = "$want"
done <<'LIST'
__MANIFEST__
LIST
test "$(find "$releases/.incoming-$rid" -type f | wc -l)" = __COUNT__
mv -- "$releases/.incoming-$rid" "$releases/$release"
phase=switch
umask 077
cp -a "$envfile" "$envbak"
python3 - "$envfile" "$releases/$release" <<'PY'
import os, shutil, sys
path, release = sys.argv[1], sys.argv[2]
lines = open(path, encoding='utf-8', newline='').read().split('\n')
out, n = [], 0
for line in lines:
    if line.startswith('VPN_MINI_APP_STATIC_DIR='):
        line = 'VPN_MINI_APP_STATIC_DIR=' + release
        n += 1
    out.append(line)
if n != 1:
    raise SystemExit('static_dir_line_not_unique')
tmp = path + '.mini.tmp'
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
    f.write('\n'.join(out))
shutil.copystat(path, tmp)
os.replace(tmp, path)
PY
changed=1
test "$(diff <(grep -v '^VPN_MINI_APP_STATIC_DIR=' "$envbak") <(grep -v '^VPN_MINI_APP_STATIC_DIR=' "$envfile") | wc -l)" = 0
test "$(grep '^VPN_MINI_APP_STATIC_DIR=' "$envfile")" = "VPN_MINI_APP_STATIC_DIR=$releases/$release"
phase=restart
systemctl restart detourvpn-beta-api.service
for a in $(seq 1 30); do
  if systemctl is-active --quiet detourvpn-beta-api.service && [ "$(ready)" = 200 ]; then break; fi
  [ "$a" -lt 30 ] || { echo rollout=readiness_failed; false; }; sleep 1
done
phase=verify_served
page="$(curl -sS http://127.0.0.1:28000/mini-app/)"
case "$page" in *__JS__*) ;; *) echo served_index_unexpected; false ;; esac
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/mini-app/assets/__JS__)" = 200
test "$(curl -sS http://127.0.0.1:28000/mini-app/assets/__JS__ | sha256sum | cut -d ' ' -f1)" = __JSSHA__
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/mini-app/assets/__CSS__)" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/nodes)" = 401
echo release_dir=new
echo served_js_hash_matches=true
echo rollout=success; echo readiness=200
changed=0; trap - ERR; cleanup_stage
"""


def build() -> tuple[bytes, dict[str, str], str]:
    files = sorted(p for p in DIST.rglob("*") if p.is_file())
    if not files or not (DIST / "index.html").is_file():
        raise SystemExit("dist is missing; run npm run build first")
    manifest = {p.relative_to(DIST).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    release = hashlib.sha256(
        "".join(f"{rel}:{digest}\n" for rel, digest in sorted(manifest.items())).encode()
    ).hexdigest()[:32]
    buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for rel in sorted(manifest):
            data = (DIST / rel).read_bytes()
            info = tarfile.TarInfo(rel)
            info.size, info.mode, info.mtime = len(data), 0o640, now
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue(), manifest, release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    tar_bytes, manifest, release = build()
    js = next(rel for rel in manifest if rel.startswith("assets/index-") and rel.endswith(".js"))
    css = next(rel for rel in manifest if rel.startswith("assets/index-") and rel.endswith(".css"))
    index = (DIST / "index.html").read_text(encoding="utf-8")
    assert js.split("/")[1] in index and css.split("/")[1] in index
    script = (
        SCRIPT.replace("__EXPECTED_HOST__", EXPECTED_HOST)
        .replace("__CURRENT__", CURRENT_RELEASE)
        .replace("__TARSHA__", hashlib.sha256(tar_bytes).hexdigest())
        .replace("__MANIFEST__", "\n".join(f"{d} {r}" for r, d in sorted(manifest.items())))
        .replace("__COUNT__", str(len(manifest)))
        .replace("__JSSHA__", manifest[js])
        .replace("__JS__", js.split("/")[1])
        .replace("__CSS__", css.split("/")[1])
    )
    print(f"release={release} files={len(manifest)} tar_bytes={len(tar_bytes)}")
    if not args.execute:
        print("mode=preflight_only; run with --execute")
        return 0
    run_id = uuid.uuid4().hex
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY), f"root@{HOST}",
    ]
    scp = [
        "scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
        "-i", str(IDENTITY),
    ]
    stage = f"/run/detourvpn-mini-{run_id}"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "release.tar.gz").write_bytes(tar_bytes)
        (t / "deploy.sh").write_bytes(script.replace("\r", "").encode("utf-8"))
        cmd = f"set -eu; test ! -e '{stage}'; install -d -o root -g root -m 0700 '{stage}'"
        subprocess.run(ssh + [cmd], check=True)  # noqa: S603
        for f in sorted(t.iterdir()):
            for attempt in range(1, 5):
                done = subprocess.run(scp + [str(f), f"root@{HOST}:{stage}/{f.name}"], check=False)  # noqa: S603
                if done.returncode == 0:
                    break
                if attempt == 4:
                    raise SystemExit(f"upload_failed file={f.name}")
    result = subprocess.run(  # noqa: S603
        ssh + [f"chmod 0700 '{stage}/deploy.sh' && bash '{stage}/deploy.sh' '{run_id}' '{release}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    wanted = {"rollout", "rollback_phase", "readiness", "release_dir", "served_js_hash_matches"}
    print("\n".join(x for x in result.stdout.splitlines() if x.split("=")[0] in wanted))
    print(f"exit_code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

"""Deploy the redesigned admin panel to the isolated beta host.

Files: API ``admin.py`` (new read-only ``GET /api/admin/v1/beta-invites``) and the static panel
(index.html, admin.js, format.js, style.css). No migration, no database change. Dry run by default
(read-only host check); ``--execute`` replaces the files and restarts only the beta API. The host
must hold a version of each file that exists in git history (CRLF-normalised hash), otherwise
nothing is changed. Old files are copied first and restored if any later check fails.
Sources are git HEAD blobs; nothing secret is printed.
"""

# ruff: noqa: E501, S603

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
IDENTITY = (
    Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ssh" / "detourvpn-beta-20261026"
)
ROOT = "/opt/detourvpn-beta"
FILES = [
    "services/api/src/vpn_api/admin.py",
    "apps/admin/static/index.html",
    "apps/admin/static/admin.js",
    "apps/admin/static/format.js",
    "apps/admin/static/style.css",
]
NEW_FILES = {"apps/admin/static/format.js"}


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout  # noqa: S603, S607


def blob(path: str, rev: str = "HEAD") -> bytes:
    return git("show", f"{rev}:{path}").replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def history(path: str) -> list[str]:
    """Hashes of every committed version of the file (LF-normalised), newest first."""
    revisions = git("log", "--format=%H", "--", path).decode().split()
    return list(dict.fromkeys(sha(blob(path, rev)) for rev in revisions))


PROGRAM = r"""set -Eeuo pipefail
set +x
mode="$1"; rid="$2"
[[ "$mode" =~ ^(check|apply)$ ]] || exit 2
[[ "$rid" =~ ^[0-9a-f]{32}$ ]] || exit 2
root=/opt/detourvpn-beta; stage="/run/detourvpn-admin-$rid"; backup="/root/detourvpn-pre-admin-$rid"
changed=0; phase=preflight
ready() { curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true; }
restore() {
  while read -r rel old; do
    [ -z "$rel" ] && continue
    if [ -e "$backup/$(echo "$rel" | tr / _).absent" ]; then rm -f -- "$root/$rel"; else cp -a "$backup/$(echo "$rel" | tr / _)" "$root/$rel"; fi
  done <<'LIST'
__RESTORE__
LIST
}
rollback() {
  rc=$?; echo rollback_phase="$phase"
  if [ "$changed" = 1 ]; then
    systemctl stop detourvpn-beta-api.service || true
    restore || true
    systemctl start detourvpn-beta-api.service || true
    for i in $(seq 1 30); do [ "$(ready)" = 200 ] && { echo rollout=code_rolled_back; rm -rf -- "/run/detourvpn-admin-$rid"; exit "$rc"; }; sleep 1; done
    echo rollout=rollback_readiness_failed; exit 70
  fi
  exit "$rc"
}
trap rollback ERR
test "$(hostname -s)" = __EXPECTED_HOST__
systemctl is-active --quiet detourvpn-beta-api.service
test "$(ready)" = 200
test "$(df -Pk / | awk 'NR==2{print $4}')" -gt 500000
phase=verify_baselines
while read -r rel allowed; do
  [ -z "$rel" ] && continue
  if [ ! -e "$root/$rel" ]; then
    case ",$allowed," in *",absent,"*) echo "baseline_ok=$rel"; continue ;; *) echo "baseline_missing=$rel"; false ;; esac
  fi
  have="$(sed 's/\r$//' "$root/$rel" | sha256sum | cut -d ' ' -f1)"
  case ",$allowed," in *",$have,"*) echo "baseline_ok=$rel" ;; *) echo "baseline_mismatch=$rel"; false ;; esac
done <<'LIST'
__BASELINES__
LIST
if [ "$mode" = check ]; then echo rollout=check_only_nothing_changed; exit 0; fi
phase=verify_uploaded
while read -r want name; do [ -z "$name" ] && continue; test "$(sha256sum "$stage/$name" | cut -d ' ' -f1)" = "$want"; done <<'LIST'
__UPLOADED__
LIST
phase=backup
umask 077
install -d -o root -g root -m 0700 "$backup"
while read -r rel old; do
  [ -z "$rel" ] && continue
  if [ -e "$root/$rel" ]; then cp -a "$root/$rel" "$backup/$(echo "$rel" | tr / _)"; else : > "$backup/$(echo "$rel" | tr / _).absent"; fi
done <<'LIST'
__RESTORE__
LIST
changed=1
phase=replace
systemctl stop detourvpn-beta-api.service
while read -r name rel; do
  [ -z "$name" ] && continue
  new="$root/$rel.admin-$rid"
  ref="$root/$rel"; [ -e "$ref" ] || ref="$root/apps/admin/static/index.html"
  install -o "$(stat -c '%U' "$ref")" -g "$(stat -c '%G' "$ref")" -m "$(stat -c '%a' "$ref")" "$stage/$name" "$new"
  mv -f -- "$new" "$root/$rel"
done <<'LIST'
__MAP__
LIST
while read -r want rel; do [ -z "$rel" ] && continue; test "$(sha256sum "$root/$rel" | cut -d ' ' -f1)" = "$want"; done <<'LIST'
__INSTALLED__
LIST
phase=api_startup
systemctl start detourvpn-beta-api.service
for a in $(seq 1 30); do
  if systemctl is-active --quiet detourvpn-beta-api.service && [ "$(ready)" = 200 ]; then break; fi
  [ "$a" -lt 30 ] || { echo rollout=readiness_failed; false; }; sleep 1
done
phase=verify
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/admin/v1/beta-invites)" = 401
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/admin/format.js)" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/admin/)" = 200
echo rollout=success; echo readiness=200
changed=0; trap - ERR; rm -rf -- "/run/detourvpn-admin-$rid"
"""


def build(execute: bool) -> int:
    run_id = uuid.uuid4().hex
    files = {p: blob(p) for p in FILES}
    names = {p: p.replace("/", "_") for p in files}
    baselines = "\n".join(
        f"{p} {','.join((['absent'] if p in NEW_FILES else []) + history(p))}" for p in FILES
    )
    restore = "\n".join(f"{p} {'absent' if p in NEW_FILES else 'present'}" for p in FILES)
    program = (
        PROGRAM.replace("__EXPECTED_HOST__", EXPECTED_HOST)
        .replace("__BASELINES__", baselines)
        .replace("__RESTORE__", restore)
        .replace("__UPLOADED__", "\n".join(f"{sha(files[p])} {names[p]}" for p in files))
        .replace("__MAP__", "\n".join(f"{names[p]} {p}" for p in files))
        .replace("__INSTALLED__", "\n".join(f"{sha(files[p])} {p}" for p in files))
    )
    ssh = [
        "ssh",
        "-o",
        "BatchMode=yes", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(IDENTITY),
        f"root@{HOST}",
    ]
    scp = [
        "scp",
        "-o",
        "BatchMode=yes", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(IDENTITY),
    ]
    stage = f"/run/detourvpn-admin-{run_id}"
    if execute:
        subprocess.run(
            ssh + [f"set -eu; test ! -e '{stage}'; install -d -o root -g root -m 0700 '{stage}'"],
            check=True,
        )  # noqa: S603
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            for p, data in files.items():
                (temp / names[p]).write_bytes(data)  # bytes: Windows text mode would add CRs
            for item in temp.iterdir():
                for attempt in range(1, 5):
                    if (
                        subprocess.run(
                            scp + [str(item), f"root@{HOST}:{stage}/{item.name}"], check=False
                        ).returncode
                        == 0
                    ):  # noqa: S603
                        break
                    if attempt == 4:
                        raise SystemExit(f"upload_failed file={item.name}")
        code = "apply"
    else:
        code = "check"
    # The program travels over stdin; positional arguments follow "--".
    result = subprocess.run(
        ssh + ["bash", "-s", "--", code, run_id],
        input=program.replace("\r", "").encode(),
        capture_output=True,
        check=False,
    )  # noqa: S603
    wanted = {"rollout", "rollback_phase", "readiness", "baseline_ok", "baseline_mismatch"}
    print(
        "\n".join(
            line
            for line in result.stdout.decode("utf-8", "replace").splitlines()
            if line.split("=")[0] in wanted
        )
    )
    print(f"mode={code}; exit_code={result.returncode}")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    return build(parser.parse_args().execute)


if __name__ == "__main__":
    sys.exit(main())

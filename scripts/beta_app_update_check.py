# ruff: noqa: E501, S603, S607
"""Read-only: why does the in-app update publish fail on the beta API host? (ADR-107)

Changes nothing on the host. Over the deploy scripts' SSH key it reads:
  - SHA-256 of the host's API files that serve the update (app_update.py, main.py, config.py),
    and matches each one against this repository's git history to name the commit it came from;
  - whether the host's app_update.py carries a signing public key, and whether it is this repo's;
  - which update files are in the upload folder and whether the API user can read them;
  - whether the API environment names the update variables (names only, never values);
  - the API's own answer for the signed manifest (local HTTP status code only).

    uv run --frozen python scripts/beta_app_update_check.py
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.deploy_beta_api_latest import HOST  # noqa: E402
from scripts.publish_beta_app_update import SSH_OPTIONS  # noqa: E402

FILES = ("app_update.py", "main.py", "config.py")
API_DIR = "services/api/src/vpn_api"
# Fixed remote command; nothing user-supplied reaches the remote shell.
REMOTE = r"""set -u
root=/opt/detourvpn-beta/services/api/src/vpn_api
for f in app_update.py main.py config.py; do
  if [ -f "$root/$f" ]; then echo "file $f $(sha256sum "$root/$f" | cut -d ' ' -f1)"; else echo "file $f missing"; fi
done
key=$(grep -E '^UPDATE_SIGNING_PUBLIC_KEY = ' "$root/app_update.py" 2>/dev/null | head -n1 | sed -E 's/^UPDATE_SIGNING_PUBLIC_KEY = "(.*)"\s*$/\1/')
echo "host_key=${key:-<none>}"
grep -q '/api/v1/app/signed-update-manifest' "$root/main.py" 2>/dev/null && echo route_signed=yes || echo route_signed=no
grep -q '/api/v1/app/download/' "$root/main.py" 2>/dev/null && echo route_download=yes || echo route_download=no
dir=/var/lib/detourvpn-beta/app-update
if [ -d "$dir" ]; then
  echo "upload_dir=present $(stat -c '%U:%G %a' "$dir")"
  for p in "$dir"/*; do
    [ -e "$p" ] || continue
    n=$(basename "$p")
    case "$n" in *.apk|app-update.json) ;; *) continue ;; esac
    r=no; runuser -u detourvpn-beta -- test -r "$p" && r=yes
    echo "upload $n $(stat -c '%U:%G %a %s' "$p") api_user_can_read=$r sha256=$(sha256sum "$p" | cut -c1-16)"
  done
else
  echo upload_dir=missing
fi
for v in VPN_APP_UPDATE_MANIFEST_FILE VPN_APP_UPDATE_DOWNLOAD_DIR VPN_APP_UPDATE_PUBLIC_KEY; do
  grep -q "^$v=" /etc/detourvpn-beta/api.env 2>/dev/null && echo "env $v=set" || echo "env $v=unset"
done
echo "api_unit=$(systemctl is-active detourvpn-beta-api.service)"
echo "local_signed_manifest_http=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/app/signed-update-manifest || true)"
"""


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def history_match(path: str, digest: str) -> str:
    """The newest commit whose version of path has this SHA-256, or a note when none does."""
    for rev in git("log", "--format=%h", "--", path).split():
        blob = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, check=False)
        if blob.returncode == 0 and hashlib.sha256(blob.stdout).hexdigest() == digest:
            return rev
    return "no commit in this repository"


def main() -> int:
    local_key = re.search(
        r'^UPDATE_SIGNING_PUBLIC_KEY = "(.*)"\s*$',
        Path(API_DIR, "app_update.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    result = subprocess.run(  # noqa: S603 - fixed command
        ["ssh", *SSH_OPTIONS, f"root@{HOST}", REMOTE],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode == 255:
        print("ssh_failed: the host could not be reached; try again")
        return 1
    host_key = ""
    for line in result.stdout.splitlines():
        parts = line.split()
        if (
            line.startswith("file ")
            and len(parts) == 3
            and parts[1] in FILES
            and parts[2] != "missing"
        ):
            name, digest = parts[1], parts[2]
            print(f"host {name}: matches {history_match(f'{API_DIR}/{name}', digest)}")
        elif line.startswith("host_key="):
            host_key = line.split("=", 1)[1]
            same = local_key is not None and host_key == local_key.group(1)
            state = (
                "none (empty)"
                if host_key == "<none>"
                else ("same as this repo" if same else "DIFFERENT from this repo")
            )
            print(f"host signing key: {state}")
        else:
            print(line)
    print()
    print("How to read this:")
    print("- signing key 'none' or 'DIFFERENT', or route_* = no: the host runs older API code.")
    print("  Deploy it first: .\\scripts\\enable_app_update.ps1 -Publish -ApiBaselineRev <commit>,")
    print("  where <commit> is the one named for app_update.py above (the code the host runs now).")
    print("- api_user_can_read=no: the API cannot read the uploaded file (permissions).")
    print(
        "- local_signed_manifest_http=404 with env ...=unset is expected after a rolled-back publish."
    )
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

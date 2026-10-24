#!/bin/bash
# Owner-authorized, bounded I-028 staging A only. Enter sudo only in a local SSH PTY.
set -Eeuo pipefail
umask 077
clock_override=0
own_helper=0
function i028_failure() {
    local failure_line=$1 failure_code=$2
    trap - ERR
    if test "$own_helper" = 1; then
        /usr/local/libexec/detourvpn-i028-control stop || true
    fi
    if test "$clock_override" = 1; then
        timedatectl revert ens3 || true
        systemctl restart systemd-timesyncd.service || true
    fi
    echo "i028_install_failed_line=${failure_line}" >&2
    exit "$failure_code"
}
trap 'i028_failure "$LINENO" "$?"' ERR
test "$(id -u)" = 0
test -t 0
SOURCE=$(cd -- "$(dirname -- "$0")/../.." && pwd)
test "$SOURCE" = /home/vpn-deploy/detourvpn-i028-stage
ip -4 address show dev ens3 | grep -q '45\.10\.166\.106/'
test "$(dpkg --print-architecture)" = amd64
test "$(. /etc/os-release; echo "$VERSION_ID")" = 24.04
if test "$#" = 1 && test "$1" = --archive-closed; then
    # Explicit restart only: independent closure checks, then recoverable moves.
    cd "$SOURCE"
    sha256sum --check release.sha256 >/dev/null
    python3 "$SOURCE/infra/staging-v3/archive_closed.py"
else
    test "$#" = 0
fi
if ! timedatectl show -p NTPSynchronized --value | grep -qx yes; then
    # Existing time service, runtime link override only. The two fixed Cloudflare
    # IPv4 servers were probed successfully from A before this owner action.
    # Preserve any existing link-specific configuration rather than overwriting.
    systemctl is-active --quiet systemd-timesyncd.service
    test -z "$(timedatectl show-timesync -p LinkNTPServers --value)"
    echo 'I028: selecting the verified reachable Cloudflare NTP servers for ens3 (runtime only).'
    clock_override=1
    timedatectl ntp-servers ens3 162.159.200.1 162.159.200.123
    systemctl restart systemd-timesyncd.service
    for attempt in $(seq 1 60); do
        if timedatectl show -p NTPSynchronized --value | grep -qx yes; then break; fi
        sleep 1
    done
    timedatectl show -p NTPSynchronized --value | grep -qx yes
fi
test "$(df --output=avail / | tail -1)" -gt 1500000
test "$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)" -gt 350000
for path in /opt/detourvpn-i028 /opt/detourvpn-node /etc/detourvpn-node /run/detourvpn-node /var/lib/detourvpn-node /etc/sudoers.d/detourvpn-i028; do
    test ! -e "$path"
    test ! -L "$path"
done
for tool in ip iptables iptables-save nft nsenter openssl curl tar systemd-run; do
    command -v "$tool" >/dev/null
done
test -z "$(ss -H -ltn '( sport = :443 )')"
test -z "$(ss -H -lun '( sport = :51901 or sport = :51902 )')"
cd "$SOURCE"
sha256sum --check release.sha256 >/dev/null
ROOT=/opt/detourvpn-i028
install -d -m 0755 "$ROOT" /opt/detourvpn-node/current /usr/local/libexec
for name in window_daemon.py window_gate.py window_peer.py; do
    cp "services/node-agent/$name" "$ROOT/"
done
cp infra/staging-v3/{runtime.py,tls_relay.py} "$ROOT/"
install -d -m 0755 "$ROOT/vpn_api/tunnels"
touch "$ROOT/vpn_api/__init__.py" "$ROOT/vpn_api/tunnels/__init__.py"
for name in keys.py v3_contracts.py window_journal.py window_runtime.py; do
    cp "services/api/src/vpn_api/tunnels/$name" "$ROOT/vpn_api/tunnels/"
done
cp services/node-agent/window-requirements.txt "$ROOT/requirements.txt"
install -m 0755 artifacts/awg /opt/detourvpn-node/current/awg
install -m 0755 artifacts/amneziawg-go /opt/detourvpn-node/current/amneziawg-go
echo '4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce  /opt/detourvpn-node/current/awg' | sha256sum --check >/dev/null
echo '84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe  /opt/detourvpn-node/current/amneziawg-go' | sha256sum --check >/dev/null
curl --fail --silent --show-error --location https://github.com/astral-sh/uv/releases/download/0.11.17/uv-x86_64-unknown-linux-gnu.tar.gz --output "$ROOT/uv.tar.gz"
echo "0017ccecaeb4d431d7f93b583ebff0c5c38e00eb734fcf13d05f72ca419125fe  $ROOT/uv.tar.gz" | sha256sum --check >/dev/null
tar -xzf "$ROOT/uv.tar.gz" -C "$ROOT" --strip-components=1 uv-x86_64-unknown-linux-gnu/uv
export UV_PYTHON_INSTALL_DIR="$ROOT/python"
export UV_CACHE_DIR="$ROOT/uv-cache"
"$ROOT/uv" python install --no-bin 3.13.12
"$ROOT/uv" venv "$ROOT/.venv" --python 3.13.12
"$ROOT/uv" pip sync --python "$ROOT/.venv/bin/python" --require-hashes "$ROOT/requirements.txt"
chown -R root:root "$ROOT" /opt/detourvpn-node
find "$ROOT" /opt/detourvpn-node -type d -exec chmod go-w {} +
find "$ROOT" /opt/detourvpn-node -type f -exec chmod go-w {} +
cat >/usr/local/libexec/detourvpn-i028-control <<'PY'
#!/opt/detourvpn-i028/.venv/bin/python
import sys
sys.path.insert(0, '/opt/detourvpn-i028')
from runtime import main
raise SystemExit(main())
PY
chmod 0755 /usr/local/libexec/detourvpn-i028-control
own_helper=1
/usr/local/libexec/detourvpn-i028-control prepare
cat >/etc/sudoers.d/detourvpn-i028 <<'SUDO'
vpn-deploy ALL=(root) NOPASSWD: /usr/local/libexec/detourvpn-i028-control status, /usr/local/libexec/detourvpn-i028-control manifest, /usr/local/libexec/detourvpn-i028-control configure, /usr/local/libexec/detourvpn-i028-control start-agent, /usr/local/libexec/detourvpn-i028-control stop-agent, /usr/local/libexec/detourvpn-i028-control crash-agent, /usr/local/libexec/detourvpn-i028-control stop
SUDO
chmod 0440 /etc/sudoers.d/detourvpn-i028
visudo -cf /etc/sudoers.d/detourvpn-i028 >/dev/null
echo 'I028 bounded slots prepared. Cleanup deadline is already running (30 minutes).'

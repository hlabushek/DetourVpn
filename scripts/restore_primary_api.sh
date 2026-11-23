#!/usr/bin/env bash
# Restore and stabilise the PRIMARY beta API (Helsinki host). Run as root on that host:
#   bash /root/restore_primary_api.sh
#
# What it does (idempotent, safe to re-run):
#   1. Checks that the deployed code matches the reviewed commits (b5abc3f, 9aeb196).
#   2. Checks the database URL shape with the API's own parser. Never prints the URL or password.
#   3. Makes the systemd unit restart itself after crashes, without giving up after a burst.
#   4. Clears a failed start-rate limit and restarts the API.
#   5. Waits for /api/v1/ready = 200 and checks the public mini-app path.
#   6. Prints five latency samples of /ready.
#
# It does NOT change the database password or the URL. Those are set by hand (see the chat notes).

set -euo pipefail

readonly APP=/opt/detourvpn-beta
readonly ENV_FILE=/etc/detourvpn-beta/api.env
readonly UNIT=detourvpn-beta-api
readonly PORT=28000
# Host-specific values are NOT in this file (audit M9). They come from RESTORE_ENV, a root-only
# file outside the repository (mode 600), with exactly these lines:
#   PUBLIC_IP=<public IPv4 of this host>
#   DATABASE_HOST=<managed database host name, without port or database>
readonly RESTORE_ENV="${RESTORE_ENV:-/root/detourvpn-restore.env}"
readonly SHA_CONNECTION=dfab57f397ccc10b4af16abed852ac143b426004313e3334445931876bcb834b
readonly SHA_WINDOW_SERVICE=fd4414a92ea32ed969884d0664e0283b92cac265f4e17f24a0a54fab33cb4b80

log() { printf '[restore] %s\n' "$*"; }
die() { printf '[restore] FAIL: %s\n' "$*" >&2; exit 1; }

[[ -f "$RESTORE_ENV" ]] || die "$RESTORE_ENV missing (see the header of this script)"
[[ "$(stat -c '%a %u' "$RESTORE_ENV")" == "600 0" ]] || die "$RESTORE_ENV must be mode 600, owned by root"
while IFS='=' read -r key value; do
  case "$key" in
    PUBLIC_IP) PUBLIC_IP="$value" ;;
    DATABASE_HOST) DATABASE_HOST="$value" ;;
    ''|'#'*) ;;
    *) die "unexpected line in $RESTORE_ENV" ;;
  esac
done < "$RESTORE_ENV"
[[ "${PUBLIC_IP:-}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "PUBLIC_IP missing or invalid in $RESTORE_ENV"
[[ "${DATABASE_HOST:-}" =~ ^[a-z0-9.-]+\.[a-z]{2,}$ ]] || die "DATABASE_HOST missing or invalid in $RESTORE_ENV"

[[ "${EUID}" -eq 0 ]] || die "run as root"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE missing"

# 1. Code matches the reviewed versions.
check_code() {
  local rel=$1 want=$2 got
  got=$(sha256sum "$APP/$rel" | cut -d' ' -f1)
  if [[ "$got" == "$want" ]]; then
    log "code ok: $rel"
  else
    die "$rel differs from the reviewed version. Copy the reviewed file first (see the chat)."
  fi
}
check_code services/api/src/vpn_api/db/connection.py "$SHA_CONNECTION"
check_code services/api/src/vpn_api/tunnels/window_service.py "$SHA_WINDOW_SERVICE"

# 2. Database URL: right host, verify-full, parsed by the API's own code. Nothing is printed.
grep -q "^VPN_DATABASE_URL=\"postgresql+psycopg://vpn_api:" "$ENV_FILE" \
  || die "VPN_DATABASE_URL has the wrong shape (expected postgresql+psycopg://vpn_api:...)"
grep -Fq "@${DATABASE_HOST}/neondb?sslmode=verify-full\"" "$ENV_FILE" \
  || die "VPN_DATABASE_URL does not point to the configured database host with sslmode=verify-full"
(
  set -a; . "$ENV_FILE"; set +a
  cd "$APP"
  PYTHONPATH=services/api/src "$APP/.venv/bin/python" -c '
import os, sys
from pydantic import SecretStr
from vpn_api.db.url import parse_database_url
try:
    parse_database_url(SecretStr(os.environ["VPN_DATABASE_URL"]))
except ValueError:
    sys.exit(1)
'
) || die "the API parser rejects VPN_DATABASE_URL (check the password characters: letters and digits only)"
log "database URL accepted by the API parser (value not shown)"

# 3. Crash recovery: restart after any failure, never stop retrying because of a burst of failures.
install -d /etc/systemd/system/${UNIT}.service.d
cat > /etc/systemd/system/${UNIT}.service.d/stability.conf <<'CONF'
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=3
CONF
systemctl daemon-reload
log "restart policy set: Restart=always, no start-rate limit"

# 4. Clear any earlier failed state, then restart.
systemctl reset-failed "$UNIT" 2>/dev/null || true
systemctl restart "$UNIT"
log "restarted $UNIT"

# 5. Wait for readiness.
code=""
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:${PORT}/api/v1/ready" || true)
  [[ "$code" == "200" ]] && break
  sleep 2
done
if [[ "$code" != "200" ]]; then
  journalctl -u "$UNIT" -n 30 --no-pager || true
  die "/api/v1/ready is ${code:-no answer}. See the journal above."
fi
log "/api/v1/ready = 200"

mini=$(curl -sk -o /dev/null -w '%{http_code}' -m 8 "https://${PUBLIC_IP}/mini-app/" || true)
log "public mini-app: ${mini:-no answer} (expected 200)"

# 6. Latency sample of the real database path.
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w '  ready #'"$i"' %{http_code} %{time_total}s\n' -m 5 "http://127.0.0.1:${PORT}/api/v1/ready" || true
  sleep 2
done

log "done. The API restarts itself after a crash now. Check later with: journalctl -u ${UNIT} -n 50 --no-pager"

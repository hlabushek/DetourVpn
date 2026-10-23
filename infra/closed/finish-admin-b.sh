#!/bin/bash
# Owner-interactive completion; no bot credentials, webhook changes or fake auth.
set -Eeuo pipefail
umask 077
trap 'echo "admin_setup_failed_at_line_$LINENO; no_secret_details_printed" >&2' ERR
test "$(id -u)" = 0
test -t 0
SOURCE=/home/vpn-deploy/detourvpn-admin-setup
cd "$SOURCE"
sha256sum --check --quiet release.sha256
systemctl is-active --quiet detourvpn-database.service
systemctl is-active --quiet caddy.service
systemctl is-active --quiet detourvpn-certificate.timer
test -f /opt/detourvpn/services/api/src/vpn_api/config.py
install -o root -g root -m 0644 services/api/src/vpn_api/config.py /opt/detourvpn/services/api/src/vpn_api/config.py
install -o root -g root -m 0644 scripts/admin_bootstrap.py /opt/detourvpn/scripts/admin_bootstrap.py
cd /opt/detourvpn
if test ! -e /etc/detourvpn/api.env; then
    .venv/bin/python - <<'PY'
import base64
import os
import secrets
from pathlib import Path
password = Path('/etc/detourvpn/secrets/pg-runtime').read_text().strip()
values = {
    'VPN_ENVIRONMENT': 'closed',
    'VPN_PUBLIC_ORIGIN': 'https://85.209.132.205',
    'VPN_DATABASE_URL': 'postgresql+psycopg://vpn_api:' + password + '@127.0.0.1:15433/detourvpn',
    'VPN_ENABLE_AUTH': 'false',
    'VPN_ENABLE_ADMIN': 'true',
    'VPN_CLOSED_ADMIN_ONLY': 'true',
    'VPN_ENABLE_API_DOCS': 'false',
    'VPN_ADMIN_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    'VPN_INITIAL_QUOTA_BYTES': '0',
}
path = Path('/etc/detourvpn/api.env')
with path.open('x') as output:
    os.chmod(path, 0o600)
    output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
PY
fi
set -a
source /etc/detourvpn/api.env
set +a
export PYTHONPATH=/opt/detourvpn/services/api/src
.venv/bin/python - <<'PY'
from vpn_api.config import Settings
config = Settings(_env_file=None)
assert config.closed_admin_only and not config.enable_auth and config.enable_admin
print('explicit_admin_setup_configuration_valid')
PY
systemctl enable --now detourvpn-api.service
for attempt in $(seq 1 20); do
    if curl --fail --silent http://127.0.0.1:8000/api/v1/ready >/dev/null; then break; fi
    sleep 2
done
curl --fail --silent http://127.0.0.1:8000/api/v1/ready
curl --fail --silent https://85.209.132.205/api/v1/health
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://85.209.132.205/api/v1/me)" = 503
echo 'Administrative API ready. Telegram user login remains disabled.'
echo 'Next: create your admin account with password and authenticator.'
echo 'Enrollment URI will appear ONLY in this private interactive terminal.'
echo 'Do not send it or your password/codes to chat. No terminal recording/screenshots.'
install -d -m 0700 /var/lib/detourvpn-rollout/enrollment
ENROLLMENT=/var/lib/detourvpn-rollout/enrollment/owner.uri
.venv/bin/python scripts/admin_bootstrap.py --username owner --enrollment-file "$ENROLLMENT" \
    --resume --show-enrollment-in-terminal
echo 'Admin owner enrolled. Open https://85.209.132.205/admin/ and use the next TOTP code.'

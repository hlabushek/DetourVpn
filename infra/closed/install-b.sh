#!/bin/bash
# Explicitly approved 2026-10-23: only closed control plane on staging B.
# Run in a real owner-controlled SSH terminal; no passwords in arguments/logs.
set -Eeuo pipefail
umask 077
trap 'echo "closed_rollout_preflight_failed_at_line_$LINENO; no_installation_started" >&2' ERR
test "$(id -u)" = 0
test -t 0
SOURCE=$(cd -- "$(dirname -- "$0")/../.." && pwd)
test "$SOURCE" = /home/vpn-deploy/detourvpn-rollout
ip -4 address show | grep -q '85\.209\.132\.205/'
test "$(dpkg --print-architecture)" = amd64
test "$(. /etc/os-release; echo "$VERSION_ID")" = 24.04
if test -e /opt/detourvpn; then
    echo 'Existing release found: inspect and resume manually; no overwrite.' >&2
    exit 1
fi
test ! -e /etc/detourvpn
test ! -e /etc/caddy/Caddyfile
test "$(systemctl is-active caddy || true)" != active
test -z "$(ss -H -ltn '( sport = :80 or sport = :443 or sport = :8000 or sport = :15433 )')"
test "$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)" -gt 450000
test "$(df --output=avail / | tail -1)" -gt 4000000
timedatectl show -p NTPSynchronized --value | grep -qx yes
UFW_ACTIVE=false
if ufw status | grep -q '^Status: active'; then
    UFW_ACTIVE=true
else
    # An active ufw systemd oneshot does not mean the firewall is enabled.
    # Do not enable/reset it: preserve SSH and all existing network policies.
    if nft list ruleset | grep -q 'hook input'; then
        echo 'Inactive UFW with existing nft input policy: firewall review required.' >&2
        exit 1
    fi
    if command -v iptables-legacy-save >/dev/null; then
        if iptables-legacy-save | grep -Eq '^:INPUT (DROP|REJECT)|^-A INPUT '; then
            echo 'Existing legacy input policy: firewall review required.' >&2
            exit 1
        fi
    fi
    echo 'UFW disabled; no host input filter found. Preserving that baseline.'
fi
BASELINE=/var/lib/detourvpn-rollout
test ! -e "$BASELINE"
install -d -m 0700 "$BASELINE"
ss -lntup >"$BASELINE/ports-before.txt"
nft list ruleset >"$BASELINE/firewall-before.nft"
ufw status numbered >"$BASELINE/ufw-before.txt"
systemctl list-units --type=service --state=running --no-pager >"$BASELINE/services-before.txt"
cd "$SOURCE"
sha256sum --check release.sha256 >/dev/null
on_failure() {
    echo 'Rollout failed. New API/Caddy stopped; data and private baseline retained.' >&2
    systemctl stop detourvpn-api.service caddy.service 2>/dev/null || true
}
trap on_failure ERR
# Prevent the package postinstall from publishing a default HTTP page.
systemctl mask caddy.service
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    docker.io=29.1.3-0ubuntu3~24.04.2 docker-compose-v2=2.40.3+ds1-0ubuntu1~24.04.1 \
    caddy=2.6.2-6ubuntu0.24.04.3 ca-certificates curl openssl
dpkg-query -W docker.io docker-compose-v2 caddy >"$BASELINE/packages.txt"
id detourvpn >/dev/null 2>&1 || useradd --system --home-dir /var/lib/detourvpn --shell /usr/sbin/nologin detourvpn
install -d -m 0755 /opt/detourvpn /var/lib/detourvpn/acme /usr/local/lib/detourvpn
install -d -m 0700 /etc/detourvpn /etc/detourvpn/secrets
cp -a "$SOURCE/." /opt/detourvpn/
chown -R root:root /opt/detourvpn
find /opt/detourvpn -type d -exec chmod 0755 {} +
find /opt/detourvpn -type f -exec chmod 0644 {} +
curl --fail --silent --show-error --location \
    https://github.com/astral-sh/uv/releases/download/0.11.17/uv-x86_64-unknown-linux-gnu.tar.gz \
    --output "$BASELINE/uv.tar.gz"
echo "0017ccecaeb4d431d7f93b583ebff0c5c38e00eb734fcf13d05f72ca419125fe  $BASELINE/uv.tar.gz" | sha256sum --check >/dev/null
tar -xzf "$BASELINE/uv.tar.gz" -C /usr/local/lib/detourvpn --strip-components=1 uv-x86_64-unknown-linux-gnu/uv
chmod 0755 /usr/local/lib/detourvpn/uv
export UV_PYTHON_INSTALL_DIR=/opt/detourvpn-python
export UV_CACHE_DIR=/var/cache/detourvpn-uv
UV=/usr/local/lib/detourvpn/uv
cd /opt/detourvpn
"$UV" python install 3.13.12
"$UV" sync --locked --no-dev --python 3.13.12
"$UV" venv /opt/detourvpn-certbot --python 3.13.12
"$UV" pip sync --python /opt/detourvpn-certbot/bin/python --require-hashes infra/closed/certbot-requirements.txt
# The system service runs without root and must traverse the managed runtime.
chmod -R a+rX /opt/detourvpn-python /opt/detourvpn/.venv
/opt/detourvpn-certbot/bin/certbot --version >"$BASELINE/certbot-version.txt"
python3 - <<'PY'
import os, secrets
from pathlib import Path
for name in ('pg-bootstrap', 'pg-migrator', 'pg-runtime'):
    path = Path('/etc/detourvpn/secrets') / name
    path.write_text(secrets.token_urlsafe(48) + '\n')
    path.chmod(0o600)
password = Path('/etc/detourvpn/secrets/pg-migrator').read_text().strip()
path = Path('/etc/detourvpn/migrate.env')
path.write_text('VPN_DATABASE_URL=postgresql+psycopg://vpn_migrator:' + password + '@127.0.0.1:15433/detourvpn\n')
path.chmod(0o600)
PY
install -m 0644 infra/closed/detourvpn-{database,api,certificate}.service /etc/systemd/system/
install -m 0644 infra/closed/detourvpn-certificate.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now docker.service
docker compose -f infra/closed/compose.yaml pull
PG_IMAGE=postgres:17-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826
PG_UID=$(docker run --rm --network none --entrypoint id "$PG_IMAGE" -u postgres)
PG_GID=$(docker run --rm --network none --entrypoint id "$PG_IMAGE" -g postgres)
[[ "$PG_UID" =~ ^[0-9]+$ && "$PG_GID" =~ ^[0-9]+$ ]]
# Compose file secrets are bind mounts: Docker ignores a requested secret uid.
# Container postgres reads these files; host users cannot traverse the 0700 parent.
chown "$PG_UID:$PG_GID" /etc/detourvpn/secrets/pg-*
chmod 0400 /etc/detourvpn/secrets/pg-*
systemctl enable --now detourvpn-database.service
set -a
source /etc/detourvpn/migrate.env
set +a
.venv/bin/python scripts/closed_migrate.py >"$BASELINE/migration.txt" 2>&1
unset VPN_DATABASE_URL
# Literal address makes config validation and service reload independent of env.
sed 's/{$DETOURVPN_IP}/85.209.132.205/g' infra/closed/Caddyfile.ip-bootstrap >/etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl unmask caddy.service
systemctl enable --now caddy.service
if "$UFW_ACTIVE"; then
    ufw allow 80/tcp comment 'detourvpn-closed-http01'
    ufw allow 443/tcp comment 'detourvpn-closed-https'
fi
install -d -m 0755 /var/lib/detourvpn/acme/.well-known/acme-challenge
printf 'detourvpn-acme-preflight\n' >/var/lib/detourvpn/acme/.well-known/acme-challenge/preflight
chmod 0644 /var/lib/detourvpn/acme/.well-known/acme-challenge/preflight
test "$(curl --fail --silent http://85.209.132.205/.well-known/acme-challenge/preflight)" = detourvpn-acme-preflight
CERTBOT=/opt/detourvpn-certbot/bin/certbot
"$CERTBOT" certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --staging --config-dir /etc/letsencrypt-staging --work-dir /var/lib/letsencrypt-staging \
    --logs-dir /var/log/letsencrypt-staging --cert-name detourvpn-b \
    --preferred-profile shortlived --webroot -w /var/lib/detourvpn/acme --ip-address 85.209.132.205
"$CERTBOT" certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --cert-name detourvpn-b --preferred-profile shortlived \
    --webroot -w /var/lib/detourvpn/acme --ip-address 85.209.132.205
install -d -o root -g caddy -m 0750 /etc/detourvpn/tls
# caddy needs directory traversal, but cannot read the API environment or DB keys.
chmod 0711 /etc/detourvpn
install -o root -g caddy -m 0640 /etc/letsencrypt/live/detourvpn-b/{fullchain,privkey}.pem /etc/detourvpn/tls/
sed 's/{$DETOURVPN_IP}/85.209.132.205/g' infra/closed/Caddyfile.ip >/etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
install -m 0750 infra/closed/deploy-certificate.sh /etc/letsencrypt/renewal-hooks/deploy/detourvpn
"$CERTBOT" renew --dry-run --run-deploy-hooks
systemctl enable --now detourvpn-certificate.timer
if test ! -f /etc/detourvpn/api.env; then
    echo 'database_and_tls_installed; auth_configuration_pending; API_not_started'
    echo 'Existing Telegram Serverless bot and webhook unchanged.'
    exit 0
fi
systemctl enable --now detourvpn-api.service
for attempt in $(seq 1 20); do
    if curl --fail --silent http://127.0.0.1:8000/api/v1/ready >"$BASELINE/readiness.json"; then break; fi
    sleep 2
done
curl --fail --silent http://127.0.0.1:8000/api/v1/ready
curl --fail --silent https://85.209.132.205/api/v1/health
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://85.209.132.205/api/v1/ready)" = 404
systemctl is-active detourvpn-api.service caddy.service
echo 'closed_infrastructure_installed; admin_enrollment_and_acceptance_pending'

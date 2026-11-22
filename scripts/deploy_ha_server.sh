#!/usr/bin/env bash
# Backup API host bootstrap for a fresh Ubuntu 24.04 VPS (beta, sslip.io hostname).
#
# What it does:
#   1. Updates packages and installs Caddy (official repo), ufw, git, uv (pinned).
#   2. Clones or updates the repository at REPO_REF.
#   3. Installs the API into a venv and runs it as a systemd unit on 127.0.0.1:28000.
#   4. Configures Caddy for https://<PUBLIC_IP>.sslip.io (Let's Encrypt, automatic).
#   5. Opens ufw for SSH, HTTP/HTTPS and the node UDP port (SSH first, so you are not locked out).
#
# What it does NOT do (read the README):
#   - It does not install the VPN node agent. Nodes are installed from the owner
#     workstation with scripts/install_beta_node.py over SSH, using the inventory.
#   - It does not run Docker. The repo has no API Dockerfile; the API runs under
#     systemd exactly like the existing beta API unit.
#   - It does not migrate data. See scripts/migrate_db_to_neon.sh and the README.
#   - VPN_DATABASE_URL may be a remote TLS URL (Neon). The API's own parser decides
#     (services/api/src/vpn_api/db/url.py, ADR-166); the script refuses to start if it rejects it.
#
# Secrets: none are hardcoded. Secret values are read from the environment and written
# only to /etc/detourvpn-backup/api.env (mode 0600). They are never echoed.
#
# Usage (as root, on the new server):
#   sudo -E bash scripts/deploy_ha_server.sh
# Required env (see the README): REPO_URL, VPN_DATABASE_URL, VPN_ENVIRONMENT
# Optional env: REPO_REF (default main), NODE_UDP_PORT (default 51899), PUBLIC_IP (else prompted)

set -euo pipefail

readonly APP_USER="detourvpn-backup"
readonly APP_ROOT="/opt/detourvpn-backup"
readonly REPO_DIR="${APP_ROOT}/repo"
readonly ENV_DIR="/etc/detourvpn-backup"
readonly ENV_FILE="${ENV_DIR}/api.env"
readonly API_PORT="28000"
readonly UV_VERSION="0.11.17"
readonly UV_SHA256="8ac91b3913a96c6d98d65b2fc6996064c85d0dc42a626977d337046be796c75d"

log() { printf '[deploy-ha] %s\n' "$*"; }
die() { printf '[deploy-ha] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run as root (sudo -E bash $0)"
}

require_ubuntu_2404() {
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || die "Ubuntu 24.04 required, found ${PRETTY_NAME:-unknown}"
}

read_public_ip() {
  local ip="${PUBLIC_IP:-}"
  if [[ -z "${ip}" ]]; then
    read -r -p "Server public IPv4 address: " ip </dev/tty
  fi
  local octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
  [[ "${ip}" =~ ^${octet}\.${octet}\.${octet}\.${octet}$ ]] || die "not a valid IPv4 address"
  PUBLIC_IP="${ip}"
  SITE_HOST="${PUBLIC_IP}.sslip.io"
  log "public hostname: ${SITE_HOST}"
}

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "environment variable ${name} is required (see README)"
}

validate_database_url() {
  # Uses the API's own parser (vpn_api.db.url.parse_database_url, ADR-166) so the
  # script and the API cannot disagree. The URL is read from the environment, never
  # from argv, and is never printed.
  local newline=$'\n'
  if [[ "${VPN_DATABASE_URL}" == *'"'* || "${VPN_DATABASE_URL}" == *"${newline}"* ]]; then
    die "VPN_DATABASE_URL must not contain quotes or newlines"
  fi
  (
    cd "${REPO_DIR}"
    sudo -H -u "${APP_USER}" env VPN_DATABASE_URL="${VPN_DATABASE_URL}" \
      "${REPO_DIR}/.venv/bin/python" -c '
import os, sys
from pydantic import SecretStr
from vpn_api.db.url import parse_database_url
try:
    parse_database_url(SecretStr(os.environ["VPN_DATABASE_URL"]))
except ValueError:
    sys.exit(1)
'
  ) || die "VPN_DATABASE_URL rejected by the API parser (see ADR-166 and the README)"
  log "VPN_DATABASE_URL accepted by the API parser (value not shown)"
}

install_packages() {
  log "updating packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get upgrade -y
  apt-get install -y --no-install-recommends ca-certificates curl gnupg git ufw python3 python3-venv debian-keyring debian-archive-keyring apt-transport-https

  if ! command -v caddy >/dev/null 2>&1; then
    log "installing Caddy from the official repository"
    curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' -o /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
  fi
}

install_uv() {
  if [[ -x /usr/local/bin/uv ]] && /usr/local/bin/uv --version | grep -q "${UV_VERSION}"; then
    return
  fi
  log "installing uv ${UV_VERSION} (pinned, sha256 checked)"
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL -o "${tmp}/uv.tar.gz" "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
  echo "${UV_SHA256}  ${tmp}/uv.tar.gz" | sha256sum -c -
  tar -xzf "${tmp}/uv.tar.gz" -C "${tmp}"
  install -m 0755 "${tmp}/uv-x86_64-unknown-linux-gnu/uv" /usr/local/bin/uv
  rm -rf "${tmp}"
}

create_app_user() {
  id -u "${APP_USER}" >/dev/null 2>&1 || useradd --system --home "${APP_ROOT}" --shell /usr/sbin/nologin "${APP_USER}"
  install -d -o "${APP_USER}" -g "${APP_USER}" -m 0750 "${APP_ROOT}"
}

fetch_repo() {
  if [[ -d "${REPO_DIR}/.git" ]]; then
    log "updating repository at ${REPO_REF}"
    git -C "${REPO_DIR}" fetch --tags origin
    git -C "${REPO_DIR}" checkout --detach "${REPO_REF}"
  else
    log "cloning repository at ${REPO_REF}"
    git clone "${REPO_URL}" "${REPO_DIR}"
    git -C "${REPO_DIR}" checkout --detach "${REPO_REF}"
  fi
  chown -R "${APP_USER}:${APP_USER}" "${APP_ROOT}"
}

install_api_env() {
  install -d -m 0750 "${ENV_DIR}"
  # Written with a restrictive umask; values come from the environment only.
  (
    umask 077
    {
      printf 'VPN_ENVIRONMENT=%s\n' "${VPN_ENVIRONMENT}"
      printf 'VPN_PUBLIC_ORIGIN=https://%s\n' "${SITE_HOST}"
      printf 'VPN_DATABASE_URL="%s"\n' "${VPN_DATABASE_URL}"
      # ADR-171: trust X-Forwarded-For only from the local Caddy (rate-limit subjects).
      printf 'VPN_TRUSTED_PROXY_ADDRESS=127.0.0.1\n'
    } > "${ENV_FILE}"
  )
  chown root:"${APP_USER}" "${ENV_FILE}"
  chmod 0640 "${ENV_FILE}"
  log "wrote ${ENV_FILE} (mode 0640, values not printed)"
}

install_api() {
  log "installing API dependencies from the lockfile"
  cd "${REPO_DIR}"
  sudo -H -u "${APP_USER}" env UV_PYTHON_INSTALL_DIR="${APP_ROOT}/python" /usr/local/bin/uv python install 3.13
  sudo -H -u "${APP_USER}" env UV_PYTHON_INSTALL_DIR="${APP_ROOT}/python" /usr/local/bin/uv sync --frozen --no-dev
}

install_api_unit() {
  cat > /etc/systemd/system/detourvpn-backup-api.service <<UNIT
[Unit]
Description=DetourVpn backup API (beta)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${REPO_DIR}/.venv/bin/python -m uvicorn vpn_api.main:create_app --factory --app-dir services/api/src --host 127.0.0.1 --port ${API_PORT} --limit-concurrency 32 --no-access-log --no-proxy-headers
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now detourvpn-backup-api.service
}

install_caddy() {
  # Path allowlist mirrors infra/beta/Caddyfile.ip (user API only). Admin and internal
  # paths are not exposed here; /api/internal/* and /api/v1/ready return 404.
  cat > /etc/caddy/Caddyfile <<CADDY
{
	email ${ACME_EMAIL}
}

${SITE_HOST} {
	header {
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
	}
	@internal path /api/internal/* /api/v1/ready
	handle @internal {
		respond 404
	}
	@beta_api {
		path /api/v1/auth/* /api/v1/me /api/v1/me/delete /api/v1/devices /api/v1/devices/*
		path /api/v1/nodes /api/v1/quota /api/v1/quota/*
		path /api/v1/app/*
		path /api/v1/tunnels /api/v1/tunnels/* /api/v1/window-tunnels /api/v1/window-tunnels/*
		path /api/v1/plans /api/v1/subscriptions /api/v1/rewards/* /api/v1/referrals /api/v1/referrals/*
	}
	handle @beta_api {
		reverse_proxy 127.0.0.1:${API_PORT} {
			header_up X-Forwarded-For {remote_host}
		}
	}
	handle {
		respond 404
	}
}
CADDY
  caddy validate --config /etc/caddy/Caddyfile
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy
}

configure_ufw() {
  log "configuring ufw (SSH allowed before enabling)"
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow "${NODE_UDP_PORT}/udp"
  ufw --force enable
  ufw status verbose
}

check_api_health() {
  # /api/v1/ready is blocked at the proxy by design, so check the local loopback port instead.
  sleep 3
  if curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:${API_PORT}/api/v1/ready"; then
    log "local API responds on loopback"
  else
    log "local API did not answer /api/v1/ready; check: journalctl -u detourvpn-backup-api -n 50"
  fi
}

main() {
  require_root
  require_ubuntu_2404
  require_env REPO_URL
  require_env VPN_DATABASE_URL
  require_env VPN_ENVIRONMENT
  require_env ACME_EMAIL
  REPO_REF="${REPO_REF:-main}"
  NODE_UDP_PORT="${NODE_UDP_PORT:-51899}"

  read_public_ip

  install_packages
  install_uv
  create_app_user
  fetch_repo
  install_api_env
  install_api
  validate_database_url
  install_api_unit
  install_caddy
  configure_ufw
  check_api_health

  log "done. Public API base: https://${SITE_HOST}"
  log "node agent is NOT installed by this script; see the README section 'VPN node'."
}

main "$@"

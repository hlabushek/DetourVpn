#!/bin/sh
set -eu
test "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/detourvpn-beta-live
install -d -o root -g caddy -m 0750 /etc/detourvpn-beta/tls
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/fullchain.pem" /etc/detourvpn-beta/tls/fullchain.pem
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/privkey.pem" /etc/detourvpn-beta/tls/privkey.pem
/usr/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
systemctl reload caddy

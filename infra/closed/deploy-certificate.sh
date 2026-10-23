#!/bin/sh
set -eu
test "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/detourvpn-b
install -d -o root -g caddy -m 0750 /etc/detourvpn/tls
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/fullchain.pem" /etc/detourvpn/tls/fullchain.pem
install -o root -g caddy -m 0640 "$RENEWED_LINEAGE/privkey.pem" /etc/detourvpn/tls/privkey.pem
/usr/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl reload caddy

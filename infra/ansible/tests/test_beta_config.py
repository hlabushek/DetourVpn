"""Static isolation checks for the unprovisioned closed beta templates."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BETA = ROOT / "infra" / "beta"


def test_beta_templates_have_independent_identity_and_no_host_binding() -> None:
    files = [path for path in BETA.iterdir() if path.is_file() and path.suffix != ".md"]
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)

    for forbidden in (
        "85.209.132.205",
        "45.10.166.106",
        "detourvpn-closed",
        "/opt/detourvpn/",
        "/etc/detourvpn/",
        "infra/closed/",
    ):
        assert forbidden not in joined
    assert "{$DETOURVPN_BETA_IP}" in joined


def test_beta_database_is_separate_and_loopback_only() -> None:
    compose = (BETA / "compose.yaml").read_text(encoding="utf-8")
    initializer = (BETA / "postgres-init.sh").read_text(encoding="utf-8")

    assert "name: detourvpn-beta" in compose
    assert "127.0.0.1:25433:5432" in compose
    assert "beta_postgres_data:" in compose
    assert "/etc/detourvpn-beta/secrets/" in compose
    assert "vpn_migrator" in initializer
    assert "vpn_api" in initializer
    assert "CREATE DATABASE detourvpn_beta" in initializer


def test_beta_ingress_hides_internal_routes_and_bootstrap_does_not_proxy() -> None:
    caddy = (BETA / "Caddyfile.ip").read_text(encoding="utf-8")
    bootstrap = (BETA / "Caddyfile.ip-bootstrap").read_text(encoding="utf-8")
    closed = (BETA / "Caddyfile.ip-closed").read_text(encoding="utf-8")
    https_site = caddy.split("https://{$DETOURVPN_BETA_IP} {", maxsplit=1)[1]
    node_matcher = https_site.split("@beta_node_v3 {", maxsplit=1)[1].split("\n    }", maxsplit=1)[
        0
    ]
    expected_node_paths = " ".join(
        [
            "/api/internal/v3/windows/inventory",
            "/api/internal/v3/windows/start",
            "/api/internal/v3/windows/poll",
        ]
    )

    node_match = https_site.index("@beta_node_v3 {")
    node_route = https_site.index("handle @beta_node_v3 {")
    deny_match = https_site.index("@internal path /api/internal/* /api/v1/ready")
    deny_route = https_site.index("handle @internal {")
    mini_app_match = https_site.index("@beta_mini_app {")
    mini_app_route = https_site.index("handle @beta_mini_app {")
    api_match = https_site.index("@beta_api {")
    api_route = https_site.index("handle @beta_api {")
    fallback_route = https_site.index("handle {", api_route)
    assert node_match < node_route < deny_match < deny_route < mini_app_match
    assert mini_app_match < mini_app_route < api_match < api_route < fallback_route
    assert "remote_ip {$DETOURVPN_BETA_NODE_IP}" in node_matcher
    assert "method POST" in node_matcher
    assert f"path {expected_node_paths}" in node_matcher
    assert caddy.count("reverse_proxy 127.0.0.1:28000") == 3
    assert "respond 404" in caddy
    api_matcher = https_site.split("@beta_api {", maxsplit=1)[1].split("\n\t}", maxsplit=1)[0]
    for path in (
        "/api/v1/auth/*",
        "/api/v1/me",
        "/api/v1/devices",
        "/api/v1/nodes",
        "/api/v1/quota",
        "/api/v1/tunnels",
        "/api/v1/window-tunnels",
        "/api/v1/rewards/*",
        "/api/v1/referrals",
        "/api/v1/referrals/*",
        "/api/admin/v1/*",
    ):
        assert path in api_matcher
    assert "/api/v1/telegram/webhook" not in api_matcher
    fallback = https_site.split("handle {", maxsplit=1)[-1]
    assert "respond 404" in fallback
    assert "reverse_proxy" not in fallback
    assert "reverse_proxy" not in bootstrap
    assert "respond 404" in bootstrap
    assert "reverse_proxy" not in closed
    assert "https://{$DETOURVPN_BETA_IP}" in closed
    assert "respond 404" in closed


def test_beta_certificate_renewal_uses_snap_certbot_and_exact_deploy_lineage() -> None:
    service = (BETA / "detourvpn-beta-certificate.service").read_text(encoding="utf-8")
    hook = (BETA / "deploy-certificate.sh").read_text(encoding="utf-8")

    assert "ExecStart=/snap/bin/certbot renew --quiet --cert-name detourvpn-beta-live" in service
    assert "checkend 172800" in service
    assert "TimeoutStartSec=15min" in service
    assert "ProtectHome=false" in service
    assert 'test "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/detourvpn-beta-live' in hook
    assert "/usr/bin/caddy validate" in hook
    assert " >/dev/null 2>&1" in hook
    assert "systemctl reload caddy" in hook


def test_beta_node_allowlist_never_covers_other_internal_routes() -> None:
    caddy = (BETA / "Caddyfile.ip").read_text(encoding="utf-8")
    allowed = caddy.split("path /api/internal/v3/windows/", maxsplit=1)[1].splitlines()[0]

    assert allowed == "inventory /api/internal/v3/windows/start /api/internal/v3/windows/poll"
    assert "/api/internal/v1/" not in allowed
    assert "/api/internal/v3/" not in allowed.replace("/api/internal/v3/windows/start", "").replace(
        "/api/internal/v3/windows/poll", ""
    )


def test_beta_services_only_use_beta_paths_and_ports() -> None:
    api = (BETA / "detourvpn-beta-api.service").read_text(encoding="utf-8")
    database = (BETA / "detourvpn-beta-database.service").read_text(encoding="utf-8")
    maintenance = (BETA / "detourvpn-beta-maintenance.service").read_text(encoding="utf-8")

    assert "Description=DetourVpn isolated beta control plane" in api
    assert "--port 28000" in api
    assert "/opt/detourvpn-beta" in api
    assert "/etc/detourvpn-beta/api.env" in api
    assert "infra/beta/compose.yaml" in database
    assert "/etc/detourvpn-beta/operations.env" in maintenance
    assert "scripts/beta_maintenance.py" in maintenance

    timer = (BETA / "detourvpn-beta-maintenance.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=1h" in timer
    assert "Persistent=true" in timer


def test_beta_backup_service_uses_a_private_single_archive_slot() -> None:
    service = (BETA / "detourvpn-beta-backup.service").read_text(encoding="utf-8")
    timer = (BETA / "detourvpn-beta-backup.timer").read_text(encoding="utf-8")

    assert "ConditionPathIsMountPoint=" not in service
    assert "scripts/beta_backup.py" in service
    assert "--archive-dir /var/lib/detourvpn-beta-backups" in service
    assert "--latest-slot" in service
    assert "--key-file /etc/detourvpn-beta/backup-secrets/backup-key" in service
    assert "ReadWritePaths=/var/lib/detourvpn-beta-backups" in service
    assert "User=detourvpn-beta" in service
    assert "Group=detourvpn-beta" in service
    assert "EnvironmentFile=/etc/detourvpn-beta/operations.env" in service
    assert "Environment=VPN_DATABASE_URL=" not in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateTmp=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=true" in service
    assert "ProtectKernelTunables=true" in service
    assert "ProtectKernelModules=true" in service
    assert "ProtectControlGroups=true" in service
    assert "RestrictSUIDSGID=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "ReadOnlyPaths=/etc/detourvpn-beta/backup-secrets/backup-key" in service
    assert "OnCalendar=*-*-* 03:17:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "Unit=detourvpn-beta-backup.service" in timer

"""Disable the beta owner identity after a compromised MFA enrollment."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.admin_recovery import contain_admin_identity  # noqa: E402
from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, choices=("owner",))
    args = parser.parse_args()
    try:
        settings = Settings(_env_file="/etc/detourvpn-beta/api.env")
        if (
            settings.environment != "closed"
            or settings.public_origin != "https://127.0.0.1"
            or not settings.enable_admin
            or not settings.closed_admin_only
            or settings.enable_auth
            or settings.enable_mini_app
            or settings.beta_invites_only
        ):
            raise ValueError("beta_admin_only_mode_required")
        engine = create_database_engine(settings)
        try:
            result = contain_admin_identity(engine, args.username)
        finally:
            engine.dispose()
        print("beta_admin_containment_complete")
        print("disabled=" + str(result["disabled"]).lower())
        print("sessions_revoked=" + str(result["sessions_revoked"]))
        print("audit_recorded=" + str(result["audit_recorded"]).lower())
        return 0
    except Exception:
        print("beta_admin_containment_failed; no secret details shown", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

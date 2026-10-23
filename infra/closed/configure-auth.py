"""Run locally in an interactive root SSH terminal; never print bot secrets."""

import base64
import getpass
import json
import os
import re
import secrets
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    destination = Path("/etc/detourvpn/api.env")
    if os.geteuid() != 0 or not sys.stdin.isatty() or destination.exists():
        print("interactive_root_and_absent_api_env_required", file=sys.stderr)
        return 1
    token = getpass.getpass("Existing DetourVpnBot BotFather token (hidden): ")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{30,128}", token):
        raise ValueError("invalid_bot_token")
    # Read-only Bot API request; no webhook, messages or bot settings are changed.
    with urllib.request.urlopen(
        "https://api.telegram.org/bot" + token + "/getMe", timeout=20
    ) as response:
        data = json.load(response)
    bot = data["result"]
    if (
        not data.get("ok")
        or bot["id"] != int(token.split(":", 1)[0])
        or bot["username"].lower() != "detourvpnbot"
    ):
        raise ValueError("wrong_existing_bot")
    runtime_password = Path("/etc/detourvpn/secrets/pg-runtime").read_text().strip()
    values = {
        "VPN_ENVIRONMENT": "closed",
        "VPN_PUBLIC_ORIGIN": "https://85.209.132.205",
        "VPN_DATABASE_URL": "postgresql+psycopg://vpn_api:"
        + runtime_password
        + "@127.0.0.1:15433/detourvpn",
        "VPN_ENABLE_AUTH": "true",
        "VPN_ENABLE_ADMIN": "true",
        "VPN_ENABLE_API_DOCS": "false",
        "VPN_AUTH_PEPPER": secrets.token_urlsafe(48),
        "VPN_TELEGRAM_WEBHOOK_SECRET": secrets.token_urlsafe(48),
        "VPN_ADMIN_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "VPN_TELEGRAM_BOT_TOKEN": token,
        "VPN_TELEGRAM_BOT_ID": str(bot["id"]),
        "VPN_TELEGRAM_BOT_USERNAME": bot["username"],
        "VPN_INITIAL_QUOTA_BYTES": "0",
    }
    with destination.open("x", encoding="utf-8") as output:
        os.chmod(destination, 0o600)
        output.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    print("auth_configuration_created; webhook_unchanged")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("auth_configuration_failed; secrets_not_printed", file=sys.stderr)
        raise SystemExit(1) from None

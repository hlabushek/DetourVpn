"""Explicit cleanup of local authentication retention; no user/account deletion."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from vpn_api.auth.service import AuthService  # noqa: E402
from vpn_api.auth.telegram import TelegramSender  # noqa: E402
from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402


def main() -> None:
    settings = Settings()
    if not settings.enable_auth or settings.telegram_bot_token is None:
        raise SystemExit("Configure local authentication before cleanup")
    engine = create_database_engine(settings)
    try:
        result = AuthService(
            engine, settings, TelegramSender(settings.telegram_bot_token)
        ).cleanup()
        # Aggregate row counts only, no IDs/subjects/secrets.
        print(result)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

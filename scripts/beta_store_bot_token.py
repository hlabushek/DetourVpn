"""Store the beta Bot API token without enabling auth or exposing it in argv/logs."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

ENV_FILE = Path("/etc/detourvpn-beta/api.env")
BOT_USERNAME = "uysdgyfudf_bot"
TOKEN_PATTERN = re.compile(r"([0-9]{5,12}):[A-Za-z0-9_-]{30,128}\Z")
PROTECTED_FLAGS = {
    "VPN_ENVIRONMENT": "closed",
    "VPN_ENABLE_ADMIN": "true",
    "VPN_CLOSED_ADMIN_ONLY": "true",
    "VPN_ENABLE_AUTH": "false",
    "VPN_ENABLE_MINI_APP": "false",
    "VPN_BETA_INVITES_ONLY": "false",
}
TOKEN_KEYS = {
    "VPN_TELEGRAM_BOT_TOKEN",
    "VPN_TELEGRAM_BOT_ID",
    "VPN_TELEGRAM_BOT_USERNAME",
}


def updated_env_text(existing: str, token: str) -> str:
    """Validate the closed beta config and append bot settings without changing others."""
    match = TOKEN_PATTERN.fullmatch(token)
    if match is None:
        raise ValueError("invalid_bot_api_token_format")

    values: dict[str, str] = {}
    for line in existing.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.startswith("VPN_"):
            continue
        if key in values:
            raise ValueError("duplicate_beta_environment_key")
        values[key] = value

    if any(values.get(key) != value for key, value in PROTECTED_FLAGS.items()):
        raise ValueError("beta_closed_admin_mode_required")
    if any(key in values for key in TOKEN_KEYS):
        raise ValueError("beta_bot_token_config_already_present")

    separator = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
    return (
        existing
        + separator
        + f"VPN_TELEGRAM_BOT_TOKEN={token}\n"
        + f"VPN_TELEGRAM_BOT_ID={match.group(1)}\n"
        + f"VPN_TELEGRAM_BOT_USERNAME={BOT_USERNAME}\n"
    )


def _store_token(token: str) -> None:
    if getattr(os, "geteuid")() != 0:  # noqa: B009
        raise ValueError("root_required")
    info = ENV_FILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("beta_environment_file_permissions_invalid")
    existing = ENV_FILE.read_text(encoding="utf-8")
    rendered = updated_env_text(existing, token)

    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".api.env.", dir=ENV_FILE.parent)
    try:
        os.fchmod(file_descriptor, 0o600)
        getattr(os, "fchown")(file_descriptor, 0, 0)  # noqa: B009
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(rendered)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, ENV_FILE)
        directory_flag = getattr(os, "O_DIRECTORY")  # noqa: B009
        directory_fd = os.open(ENV_FILE.parent, os.O_RDONLY | directory_flag)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _preflight() -> None:
    info = ENV_FILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("beta_environment_file_permissions_invalid")
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.startswith("VPN_"):
            if key in values:
                raise ValueError("duplicate_beta_environment_key")
            values[key] = value
    if any(values.get(key) != value for key, value in PROTECTED_FLAGS.items()):
        raise ValueError("beta_closed_admin_mode_required")
    if values.get("VPN_PUBLIC_ORIGIN") != "https://127.0.0.1":
        raise ValueError("beta_loopback_origin_required")
    if any(key in values for key in TOKEN_KEYS):
        raise ValueError("beta_bot_token_config_already_present")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "store"))
    args = parser.parse_args()
    try:
        if args.mode == "preflight":
            _preflight()
            print("beta_bot_token_preflight_passed; closed mode and loopback origin verified")
            return 0
        if getattr(os, "geteuid")() != 0:  # noqa: B009
            raise ValueError("root_required")
        raw = sys.stdin.buffer.readline(512).decode("ascii").rstrip("\r\n")
        if not raw or sys.stdin.buffer.read(1):
            raise ValueError("invalid_input")
        _preflight()
        _store_token(raw)
        print("beta_bot_token_stored; auth_still_disabled; api_not_restarted")
        return 0
    except Exception:
        print("beta_bot_token_store_failed; no secret details shown", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Activate invite-only auth on the isolated beta API after its Mini App is staged."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

DEPLOYED_ROOT = Path("/opt/detourvpn-beta")
ROOT = (
    DEPLOYED_ROOT
    if (DEPLOYED_ROOT / "services" / "api" / "src").is_dir()
    else Path(__file__).resolve().parents[1]
)
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from sqlalchemy import inspect, select  # noqa: E402

from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402
from vpn_api.db.models import AdminIdentity  # noqa: E402

ENV_FILE = Path("/etc/detourvpn-beta/api.env")
RELEASE_ROOT = Path("/opt/detourvpn-beta/mini-app/releases")
TOKEN_RE = re.compile(r"([0-9]{5,12}):[A-Za-z0-9_-]{30,128}\Z")
SECRET_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
RELEASE_RE = re.compile(r"[0-9a-f]{32}\Z")
REQUIRED_CLOSED = {
    "VPN_ENVIRONMENT": "closed",
    "VPN_PUBLIC_ORIGIN": "https://127.0.0.1",
    "VPN_ENABLE_ADMIN": "true",
    "VPN_CLOSED_ADMIN_ONLY": "true",
    "VPN_ENABLE_AUTH": "false",
    "VPN_ENABLE_MINI_APP": "false",
    "VPN_BETA_INVITES_ONLY": "false",
}
FEATURE_KEYS = {
    "VPN_AUTH_PEPPER",
    "VPN_TELEGRAM_WEBHOOK_SECRET",
    "VPN_MINI_APP_STATIC_DIR",
}
BACKUP_RE = re.compile(r"api\.env\.pre-auth-[0-9a-f]{32}\Z")


def parse_environment(text: str) -> tuple[list[str], dict[str, str]]:
    lines = text.splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.startswith("VPN_"):
            if key in values:
                raise ValueError("duplicate_beta_environment_key")
            values[key] = value
    return lines, values


def render_activation(
    existing: str,
    static_dir: str,
    auth_pepper: str,
    webhook_secret: str,
) -> str:
    lines, values = parse_environment(existing)
    if any(values.get(key) != value for key, value in REQUIRED_CLOSED.items()):
        raise ValueError("beta_closed_admin_mode_required")
    if any(key in values for key in FEATURE_KEYS):
        raise ValueError("beta_auth_configuration_already_present")
    token = values.get("VPN_TELEGRAM_BOT_TOKEN", "")
    token_match = TOKEN_RE.fullmatch(token)
    if (
        token_match is None
        or values.get("VPN_TELEGRAM_BOT_ID") != token_match.group(1)
        or values.get("VPN_TELEGRAM_BOT_USERNAME") != "uysdgyfudf_bot"
    ):
        raise ValueError("beta_bot_configuration_required")
    release = Path(static_dir)
    if (
        not release.is_absolute()
        or release.parent != RELEASE_ROOT
        or RELEASE_RE.fullmatch(release.name) is None
        or not release.is_dir()
        or release.is_symlink()
        or not (release / "index.html").is_file()
    ):
        raise ValueError("beta_mini_app_release_invalid")
    if not SECRET_RE.fullmatch(auth_pepper) or not SECRET_RE.fullmatch(webhook_secret):
        raise ValueError("beta_auth_secret_invalid")

    flags = {
        "VPN_CLOSED_ADMIN_ONLY": "false",
        "VPN_ENABLE_AUTH": "true",
        "VPN_ENABLE_MINI_APP": "true",
        "VPN_BETA_INVITES_ONLY": "true",
    }
    updated_lines: list[str] = []
    for line in lines:
        key, separator, _ = line.partition("=")
        updated_lines.append(f"{key}={flags[key]}" if separator and key in flags else line)
    current = existing.endswith(("\n", "\r"))
    separator = "\n" if not current and updated_lines else ""
    return (
        "\n".join(updated_lines)
        + separator
        + f"\nVPN_AUTH_PEPPER={auth_pepper}"
        + f"\nVPN_TELEGRAM_WEBHOOK_SECRET={webhook_secret}"
        + f"\nVPN_MINI_APP_STATIC_DIR={release}"
        + "\n"
    )


def _verify_database(settings: Settings) -> None:
    engine = create_database_engine(settings)
    try:
        inspector = inspect(engine)
        if not inspector.has_table("beta_invites"):
            raise ValueError("beta_invites_migration_missing")
        with engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(AdminIdentity.username, AdminIdentity.role, AdminIdentity.disabled_at)
                )
            )
        expected = {
            row.username: (row.role, row.disabled_at is not None)
            for row in rows
        }
        if expected != {"owner": ("admin", True), "beta-owner": ("admin", False)}:
            raise ValueError("beta_admin_state_not_safe")
    finally:
        engine.dispose()


def _write_atomic(path: Path, content: str) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".api.env.activate.", dir=path.parent)
    try:
        os.fchmod(file_descriptor, 0o600)
        getattr(os, "fchown")(file_descriptor, 0, 0)  # noqa: B009
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY"))  # noqa: B009
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


def _backup_environment() -> Path:
    backup = ENV_FILE.with_name(f"api.env.pre-auth-{uuid4().hex}")
    file_descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(file_descriptor, "wb") as output, ENV_FILE.open("rb") as source:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _preflight(static_dir: str) -> Settings:
    if getattr(os, "geteuid")() != 0:  # noqa: B009
        raise ValueError("root_required")
    info = ENV_FILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("beta_environment_file_permissions_invalid")
    _, values = parse_environment(ENV_FILE.read_text(encoding="utf-8"))
    if any(values.get(key) != value for key, value in REQUIRED_CLOSED.items()):
        raise ValueError("beta_closed_admin_mode_required")
    if any(key in values for key in FEATURE_KEYS):
        raise ValueError("beta_auth_configuration_already_present")
    settings = Settings(_env_file=ENV_FILE)
    _verify_database(settings)
    # Use deterministic placeholders only to validate the rendered Pydantic contract.
    rendered = render_activation(
        ENV_FILE.read_text(encoding="utf-8"),
        static_dir,
        "A" * 43,
        "B" * 43,
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=ENV_FILE.parent, prefix=".api.env.validate."
    ) as candidate:
        os.fchmod(candidate.fileno(), 0o600)
        getattr(os, "fchown")(candidate.fileno(), 0, 0)  # noqa: B009
        candidate.write(rendered)
        candidate.flush()
        validated = Settings(_env_file=candidate.name)
    if not (
        validated.enable_auth
        and validated.enable_mini_app
        and validated.beta_invites_only
        and validated.mini_app_static_dir == Path(static_dir)
    ):
        raise ValueError("beta_auth_configuration_invalid")
    return settings


def activate(static_dir: str) -> str:
    current = ENV_FILE.read_text(encoding="utf-8")
    rendered = render_activation(
        current,
        static_dir,
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
    )
    backup = _backup_environment()
    try:
        _write_atomic(ENV_FILE, rendered)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup.name


def rollback(backup_name: str) -> None:
    if getattr(os, "geteuid")() != 0:  # noqa: B009
        raise ValueError("root_required")
    if BACKUP_RE.fullmatch(backup_name) is None:
        raise ValueError("beta_environment_backup_invalid")
    backup = ENV_FILE.with_name(backup_name)
    info = backup.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("beta_environment_backup_permissions_invalid")
    content = backup.read_text(encoding="utf-8")
    _, values = parse_environment(content)
    if any(values.get(key) != value for key, value in REQUIRED_CLOSED.items()):
        raise ValueError("beta_environment_backup_not_closed")
    _write_atomic(ENV_FILE, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "activate", "rollback"))
    parser.add_argument("--mini-app-dir")
    parser.add_argument("--backup")
    args = parser.parse_args()
    try:
        if args.mode == "rollback":
            if not args.backup:
                raise ValueError("beta_environment_backup_required")
            rollback(args.backup)
            print("beta_auth_config_rolled_back")
            return 0
        if not args.mini_app_dir:
            raise ValueError("beta_mini_app_dir_required")
        _preflight(args.mini_app_dir)
        if args.mode == "preflight":
            print("beta_auth_preflight_passed; database, invites, admins and static app verified")
            return 0
        backup = activate(args.mini_app_dir)
        print("beta_auth_configured; api_not_restarted; rollback=" + backup)
        return 0
    except Exception:
        print("beta_auth_activation_failed; no secret details shown", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline enrollment; secrets are shown only with explicit private-terminal opt-in."""

import argparse
import base64
import getpass
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy import select  # noqa: E402

from vpn_api.admin import otp, password_hash  # noqa: E402
from vpn_api.auth.service import database_now  # noqa: E402
from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine, transaction  # noqa: E402
from vpn_api.db.models import AdminAction, AdminIdentity  # noqa: E402
from vpn_api.operations import exclusive_write  # noqa: E402


def pending_secret(raw: str, username: str) -> bytes:
    """Accept only our pending enrollment format; never log its contents."""
    if len(raw) > 2048 or "\n" in raw.rstrip("\n") or "\r" in raw:
        raise ValueError("invalid_pending_enrollment")
    uri = urlsplit(raw.strip())
    pairs = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
    values = dict(pairs)
    expected = {"issuer": "DetourVpn Admin", "algorithm": "SHA1", "digits": "6", "period": "30"}
    if (
        uri.scheme != "otpauth"
        or uri.netloc != "totp"
        or unquote(uri.path) != "/DetourVpn Admin:" + username
        or uri.fragment
        or set(values) != {"secret", *expected}
        or len(pairs) != len(values)
        or any(values.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("invalid_pending_enrollment")
    secret = base64.b32decode(values["secret"], casefold=False)
    if len(secret) != 20 or base64.b32encode(secret).decode() != values["secret"]:
        raise ValueError("invalid_pending_enrollment")
    return secret


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=("admin", "operator"), default="admin")
    parser.add_argument("--enrollment-file", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--show-enrollment-in-terminal", action="store_true")
    args = parser.parse_args()
    engine = None
    created_enrollment = False
    confirmed_enrollment = False
    failure_code = "preflight"
    try:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", args.username):
            raise ValueError("invalid_username")
        if args.enrollment_file.is_symlink():
            raise ValueError("enrollment_symlink_forbidden")
        enrollment = args.enrollment_file.resolve()
        if os.name == "nt":
            # POSIX mode bits do not establish a Windows ACL. Never claim they do.
            raise ValueError("bootstrap_requires_private_linux_operator_environment")
        if enrollment.is_relative_to(ROOT) or not enrollment.parent.is_dir():
            raise ValueError("enrollment_requires_private_directory_outside_checkout")
        if os.name != "nt" and enrollment.parent.stat().st_mode & 0o077:
            raise ValueError("enrollment_directory_requires_mode_0700")
        # Only reached on Linux; Windows type stubs do not expose geteuid.
        if enrollment.parent.stat().st_uid != getattr(os, "geteuid")():  # noqa: B009
            raise ValueError("enrollment_directory_owner_mismatch")
        if args.show_enrollment_in_terminal and not sys.stdin.isatty():
            raise ValueError("private_interactive_terminal_required")
        settings = Settings(_env_file=None)
        if not settings.enable_admin or settings.admin_encryption_key is None:
            raise ValueError("admin_disabled")
        engine = create_database_engine(settings)
        failure_code = "admin_identity_lookup"
        with transaction(engine) as session:
            if (
                session.scalar(
                    select(AdminIdentity.id).where(AdminIdentity.username == args.username)
                )
                is not None
            ):
                print("admin_already_exists; credentials_unchanged; use_existing_login")
                return 0
        failure_code = "pending_enrollment_validation"
        secret = None
        if enrollment.exists():
            info = enrollment.lstat()
            if (
                not args.resume
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid != getattr(os, "geteuid")()  # noqa: B009
                or info.st_nlink != 1
                or info.st_size > 2048
            ):
                raise ValueError("pending_enrollment_not_private")
            secret = pending_secret(enrollment.read_text(encoding="ascii"), args.username)
            print("resuming_unconfirmed_enrollment; existing_admin_checked_absent")
        failure_code = "password_entry_interrupted"
        password = getpass.getpass("New unique admin password (16-256 characters): ")
        if password != getpass.getpass("Repeat password: "):
            failure_code = "password_mismatch"
            raise ValueError("password_mismatch")
        if not 16 <= len(password) <= 256:
            failure_code = "password_length_requires_16_to_256_characters"
            raise ValueError("password_length")
        failure_code = "password_hash"
        hashed = password_hash(password)
        secret = secret if secret is not None else secrets.token_bytes(20)
        query = urlencode(
            {
                "secret": base64.b32encode(secret).decode(),
                "issuer": "DetourVpn Admin",
                "algorithm": "SHA1",
                "digits": "6",
                "period": "30",
            }
        )
        failure_code = "private_enrollment_file"
        if not enrollment.exists():
            exclusive_write(
                enrollment,
                ("otpauth://totp/DetourVpn%20Admin:" + args.username + "?" + query + "\n").encode(),
            )
            created_enrollment = True
        if args.show_enrollment_in_terminal:
            with open("/dev/tty", "w", encoding="utf-8") as terminal:
                terminal.write("\nPrivate authenticator enrollment URI (do not share):\n")
                terminal.write(enrollment.read_text(encoding="ascii"))
                terminal.flush()
        print("Open the private enrollment file locally and import it into your authenticator.")
        failure_code = "authenticator_code_or_database_commit"
        code = getpass.getpass("Current authenticator code (not echoed): ").encode()
        with transaction(engine) as session:
            now = database_now(session)
            otp(secret).verify(code, int(now.timestamp()))
            identity = AdminIdentity(
                username=args.username,
                role=args.role,
                password_hash=hashed,
                totp_ciphertext=Fernet(settings.admin_encryption_key.get_secret_value().encode())
                .encrypt(secret)
                .decode(),
                last_totp_step=int(now.timestamp()) // 30,
                created_at=now,
            )
            session.add(identity)
            session.flush()
            session.add(
                AdminAction(
                    admin_id=identity.id,
                    operation="admin_bootstrap",
                    reason="Local interactive password and MFA enrollment",
                    idempotency_key=uuid4(),
                    request_digest="bootstrap",
                    result={"role": args.role},
                    created_at=now,
                )
            )
        confirmed_enrollment = True
        print("admin_created; wait for the next authenticator code before login")
        return 0
    except (Exception, KeyboardInterrupt):
        # Fixed stage codes only: never format raw exceptions or entered secrets.
        print("admin_bootstrap_failed: " + failure_code, file=sys.stderr)
        return 1
    finally:
        if created_enrollment or confirmed_enrollment:
            # Remove only a newly created file or this validated, confirmed enrollment.
            args.enrollment_file.unlink(missing_ok=True)
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

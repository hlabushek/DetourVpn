"""Provision a disabled beta node and retain its one-time credential under /run."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from uuid import UUID

REPOSITORY = Path(__file__).resolve().parents[1]
API_ENV = Path("/etc/detourvpn-beta/api.env")
BETA_DATABASE = "detourvpn_beta"
BETA_DATABASE_USER = "vpn_api"
BETA_DATABASE_PORT = 25433
sys.path.insert(0, str(REPOSITORY / "services" / "api" / "src"))

from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402
from vpn_api.db.url import parse_local_database_url  # noqa: E402
from vpn_api.nodes.contracts import ProvisionNodeInput  # noqa: E402
from vpn_api.nodes.service import NodeError, NodeRegistry  # noqa: E402


class ProvisionError(RuntimeError):
    """Fixed error codes only; never include settings or credential values."""


def credential_directory() -> Path:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ProvisionError("beta_node_provision_requires_linux_root")
    directory = Path("/run/detourvpn-beta")
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ProvisionError("beta_node_runtime_directory_untrusted")
    return directory


def write_credential(path: Path, *, node_id: UUID, credential: str) -> None:
    """Create an exclusive root-private runtime file without exposing its contents."""
    payload = (
        json.dumps(
            {"version": 1, "node_id": str(node_id), "credential": credential},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(path, 0o600, follow_symlinks=False)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        raise ProvisionError("beta_node_credential_write_failed") from None


def load_beta_settings() -> Settings:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ProvisionError("beta_node_provision_requires_linux_root")
    try:
        info = API_ENV.lstat()
    except OSError:
        raise ProvisionError("beta_api_environment_file_unavailable") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o077
        or info.st_nlink != 1
        or info.st_size > 65536
    ):
        raise ProvisionError("beta_api_environment_file_untrusted")

    inherited = {key: value for key, value in os.environ.items() if key.startswith("VPN_")}
    try:
        for key in inherited:
            os.environ.pop(key, None)
        settings = Settings(_env_file=API_ENV)
    except ValueError:
        raise ProvisionError("beta_api_environment_invalid") from None
    finally:
        os.environ.update(inherited)
    return validate_beta_settings(settings)


def validate_beta_settings(settings: Settings) -> Settings:
    if not (
        settings.environment == "closed"
        and settings.enable_auth
        and settings.enable_mini_app
        and settings.beta_invites_only
    ):
        raise ProvisionError("beta_node_provision_requires_closed_invite_api")
    try:
        if settings.database_url is None:
            raise ValueError
        database = parse_local_database_url(settings.database_url)
    except ValueError:
        raise ProvisionError("beta_node_provision_requires_beta_database") from None
    if (
        database.database != BETA_DATABASE
        or database.username != BETA_DATABASE_USER
        or database.host not in {"127.0.0.1", "localhost"}
        or database.port != BETA_DATABASE_PORT
    ):
        raise ProvisionError("beta_node_provision_requires_beta_database")
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idempotency-key", required=True, type=UUID)
    arguments = parser.parse_args(argv)
    engine = None
    try:
        settings = load_beta_settings()
        destination = credential_directory() / (f"node-credential-{arguments.idempotency_key}.json")
        if destination.exists() or destination.is_symlink():
            raise ProvisionError("beta_node_credential_file_exists")
        engine = create_database_engine(settings)
        result = NodeRegistry(engine).provision(
            ProvisionNodeInput(
                idempotency_key=arguments.idempotency_key,
                name="detourvpn-beta-helsinki",
                region="helsinki",
            ),
            initial_status="disabled",
        )
        if result.replayed or result.secret is None:
            raise ProvisionError("beta_node_provision_replay_requires_operator_recovery")
        write_credential(destination, node_id=result.node_id, credential=result.secret)
        print(
            json.dumps(
                {
                    "created": True,
                    "node_id": str(result.node_id),
                    "status": "disabled",
                    "credential_path": str(destination),
                },
                sort_keys=True,
            )
        )
        return 0
    except (ProvisionError, NodeError) as error:
        code = str(error) if isinstance(error, ProvisionError) else error.code
        print(json.dumps({"created": False, "error": code}), file=sys.stderr)
        return 1
    except Exception:
        code = "beta_node_provision_failed"
        print(json.dumps({"created": False, "error": code}), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

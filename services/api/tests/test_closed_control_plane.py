"""Real PostgreSQL readiness and closed config negatives. No remote deployment."""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine
from vpn_api.main import create_app
from vpn_api.readiness import database_ready


def closed_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "closed",
        "public_origin": "https://vpn.example.invalid",
        "database_url": "postgresql+psycopg://vpn_api:test-only-password@127.0.0.1/test",
        "enable_auth": True,
        "auth_pepper": "a" * 43,
        "telegram_webhook_secret": "b" * 43,
        "telegram_bot_token": "123:" + "c" * 40,
        "telegram_bot_id": 123,
        "telegram_bot_username": "ExampleTestBot",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_closed_config_accepts_dedicated_runtime() -> None:
    assert closed_settings().environment == "closed"
    assert closed_settings(beta_invites_only=True).beta_invites_only


def test_closed_mini_app_requires_beta_invites() -> None:
    with pytest.raises(ValidationError, match="Closed Mini App requires beta invite enforcement"):
        closed_settings(enable_mini_app=True)
    assert closed_settings(enable_mini_app=True, beta_invites_only=True).beta_invites_only


def test_closed_admin_setup_requires_explicit_mode_and_keeps_user_auth_closed() -> None:
    config = closed_settings(
        enable_auth=False,
        enable_admin=True,
        closed_admin_only=True,
        admin_encryption_key=Fernet.generate_key().decode(),
        telegram_bot_token=None,
        telegram_bot_id=0,
        telegram_bot_username="",
    )
    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/me").status_code == 503
        assert client.get("/admin/").status_code == 200
        assert client.get("/api/admin/v1/users").status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "local", "enable_auth": False, "enable_admin": True},
        {"enable_auth": True, "enable_admin": True},
        {"enable_auth": False, "enable_admin": False},
        {"enable_auth": False, "enable_admin": True, "admin_encryption_key": None},
    ],
)
def test_closed_admin_setup_rejects_ambiguous_or_insecure_config(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        closed_settings(
            **{
                "closed_admin_only": True,
                "admin_encryption_key": Fernet.generate_key().decode(),
                **overrides,
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"enable_auth": False},
        {"database_url": None},
        {"database_url": "postgresql+psycopg://postgres:secret@127.0.0.1/test"},
        {"database_url": "postgresql+psycopg://vpn_api@127.0.0.1/test"},
        {"enable_api_docs": True},
        {"public_origin": "http://vpn.example.invalid"},
        {"public_origin": "https://user:secret@vpn.example.invalid"},
        {"public_origin": "https://vpn.example.invalid/path"},
        {"public_origin": "https://vpn.example.invalid/?secret=value"},
        {"public_origin": "https://vpn.example.invalid/#fragment"},
    ],
)
def test_closed_config_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        closed_settings(**overrides)


def upgrade(engine: Engine) -> None:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_engine"] = engine
    command.upgrade(config, "head")


def test_readiness_without_database_is_503_and_liveness_survives() -> None:
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        assert client.get("/api/v1/health").status_code == 200
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "dependencies_unavailable"


def test_readiness_validates_schema_not_just_tcp(engine: Engine) -> None:
    assert not database_ready(engine, least_privilege=False)
    upgrade(engine)
    assert database_ready(engine, least_privilege=False)
    assert not database_ready(engine, least_privilege=True)  # Test admin must never be runtime.
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'old_schema'"))
    assert not database_ready(engine, least_privilege=False)


def test_readiness_survives_new_app_and_engine_pool(engine: Engine) -> None:
    upgrade(engine)
    settings = Settings(_env_file=None, environment="test")
    for _ in range(2):
        with TestClient(create_app(settings, database_engine=engine)) as client:
            assert client.get("/api/v1/ready").status_code == 200
        engine.dispose()


def test_readiness_database_failure_has_no_credentials() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://unused:never-print-this@127.0.0.1:1/unavailable",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/ready")
        assert response.status_code == 503
        assert "never-print-this" not in response.text
        assert "unused" not in response.text


def test_real_runtime_role_migrations_dml_and_restart(engine: Engine) -> None:
    passwords = {role: secrets.token_urlsafe(32) for role in ("vpn_api", "vpn_migrator")}
    runtime = migrator = None
    try:
        with engine.begin() as connection:
            for role, password in passwords.items():
                statement = connection.scalar(
                    text(
                        "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', "
                        "CAST(:role AS text), CAST(:password AS text))"
                    ),
                    {"role": role, "password": password},
                )
                connection.exec_driver_sql(str(statement))
            connection.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
            connection.execute(text("GRANT USAGE, CREATE ON SCHEMA public TO vpn_migrator"))
            connection.execute(text("GRANT USAGE ON SCHEMA public TO vpn_api"))
            connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE vpn_migrator IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vpn_api"
                )
            )
        for role in passwords:
            url = engine.url.set(username=role, password=passwords[role])
            target = create_database_engine(
                Settings(_env_file=None, database_url=url.render_as_string(False))
            )
            if role == "vpn_api":
                runtime = target
            else:
                migrator = target
        assert runtime is not None and migrator is not None
        root = Path(__file__).resolve().parents[3]
        env = dict(os.environ)
        env["VPN_DATABASE_URL"] = migrator.url.render_as_string(False)
        env["VPN_ENVIRONMENT"] = "local"
        result = subprocess.run(
            [sys.executable, "scripts/closed_migrate.py"],
            cwd=root,
            env=env,
            capture_output=True,
            check=False,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert database_ready(runtime, least_privilege=True)
        user_id = uuid4()
        with runtime.begin() as connection:
            connection.execute(
                text("INSERT INTO users (id, status) VALUES (:id, 'active')"), {"id": user_id}
            )
        runtime.dispose()
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT id FROM users")) == user_id
        for statement in (
            "CREATE TABLE forbidden (id int)",
            "UPDATE alembic_version SET version_num = 'forbidden'",
            "DELETE FROM quota_ledger",
            "ALTER TABLE users ADD COLUMN forbidden int",
        ):
            with pytest.raises(DBAPIError), runtime.begin() as connection:
                connection.execute(text(statement))
    finally:
        for cleanup_target in (runtime, migrator):
            if cleanup_target is not None:
                cleanup_target.dispose()
        with engine.begin() as connection:
            # Only this test's disposable database/roles, never a developer DB.
            for role in passwords:
                if connection.scalar(
                    text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                    {"role": role},
                ):
                    connection.exec_driver_sql(f"DROP OWNED BY {role} CASCADE")
                    connection.exec_driver_sql(f"DROP ROLE {role}")

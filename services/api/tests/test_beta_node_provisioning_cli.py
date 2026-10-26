"""Beta provisioning CLI must keep credentials out of output and default to disabled."""

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from scripts import provision_beta_node as cli


class FakeSettings:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


def test_credential_file_is_private_and_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "credential.json"
    credential = "TEST_ONLY_" + "x" * 32

    cli.write_credential(path, node_id=uuid4(), credential=credential)

    saved = json.loads(path.read_text(encoding="ascii"))
    assert saved["credential"] == credential
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        cli.write_credential(path, node_id=uuid4(), credential=credential)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "local"),
        ("enable_auth", False),
        ("enable_mini_app", False),
        ("beta_invites_only", False),
        (
            "database_url",
            SecretStr("postgresql+psycopg://vpn_api:TEST_ONLY@127.0.0.1:25433/other"),
        ),
        (
            "database_url",
            SecretStr("postgresql+psycopg://other:TEST_ONLY@127.0.0.1:25433/detourvpn_beta"),
        ),
        (
            "database_url",
            SecretStr("postgresql+psycopg://vpn_api:TEST_ONLY@127.0.0.1:25434/detourvpn_beta"),
        ),
        (
            "database_url",
            SecretStr("postgresql+psycopg://vpn_api:TEST_ONLY@192.0.2.10:25433/detourvpn_beta"),
        ),
    ],
)
def test_cli_refuses_unapproved_api_mode_or_database(field: str, value: object) -> None:
    settings = {
        "environment": "closed",
        "database_url": SecretStr(
            "postgresql+psycopg://vpn_api:TEST_ONLY@127.0.0.1:25433/detourvpn_beta"
        ),
        "enable_auth": True,
        "enable_mini_app": True,
        "beta_invites_only": True,
    }
    settings[field] = value
    with pytest.raises(cli.ProvisionError, match="beta_node_provision_requires_"):
        cli.validate_beta_settings(FakeSettings(**settings))  # type: ignore[arg-type]


def test_cli_accepts_exact_local_beta_database() -> None:
    settings = FakeSettings(
        environment="closed",
        database_url=SecretStr(
            "postgresql+psycopg://vpn_api:TEST_ONLY@127.0.0.1:25433/detourvpn_beta"
        ),
        enable_auth=True,
        enable_mini_app=True,
        beta_invites_only=True,
    )

    assert cli.validate_beta_settings(settings) is settings  # type: ignore[arg-type]


def test_cli_provisions_disabled_node_and_does_not_print_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    credential = "TEST_ONLY_" + "x" * 32
    node_id = uuid4()
    received: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "load_beta_settings",
        lambda **_: FakeSettings(
            environment="closed",
            database_url=SecretStr(
                "postgresql+psycopg://vpn_api:TEST_ONLY@127.0.0.1:25433/detourvpn_beta"
            ),
            enable_auth=True,
            enable_mini_app=True,
            beta_invites_only=True,
        ),
    )
    monkeypatch.setattr(cli, "credential_directory", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "create_database_engine", lambda _: SimpleNamespace(dispose=lambda: None)
    )

    class Registry:
        def __init__(self, engine: object) -> None:
            received["engine"] = engine

        def provision(self, request: object, *, initial_status: str) -> SimpleNamespace:
            received["request"] = request
            received["initial_status"] = initial_status
            return SimpleNamespace(node_id=node_id, secret=credential, replayed=False)

    monkeypatch.setattr(cli, "NodeRegistry", Registry)
    key = uuid4()

    result = cli.main(["--idempotency-key", str(key)])

    captured = capsys.readouterr()
    assert result == 0
    assert credential not in captured.out + captured.err
    assert received["initial_status"] == "disabled"
    saved = json.loads((tmp_path / f"node-credential-{key}.json").read_text(encoding="ascii"))
    assert saved == {"credential": credential, "node_id": str(node_id), "version": 1}


def test_cli_hides_database_exception_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "load_beta_settings",
        lambda: FakeSettings(
            environment="closed",
            database_url=SecretStr(
                "postgresql+psycopg://vpn_api:TEST_ONLY@127.0.0.1:25433/detourvpn_beta"
            ),
            enable_auth=True,
            enable_mini_app=True,
            beta_invites_only=True,
        ),
    )
    monkeypatch.setattr(cli, "credential_directory", lambda: Path("/nonexistent-test-path"))
    monkeypatch.setattr(
        cli,
        "create_database_engine",
        lambda _: (_ for _ in ()).throw(RuntimeError("TEST_ONLY_SECRET_DETAIL")),
    )

    result = cli.main(["--idempotency-key", str(uuid4())])

    output = capsys.readouterr()
    assert result == 1
    assert "TEST_ONLY_SECRET_DETAIL" not in output.out + output.err
    assert "beta_node_provision_failed" in output.err

"""I-016: real local PostgreSQL restore drills; all account/traffic data synthetic."""

import base64
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Connection, Engine, select, text
from test_quota import LedgerHarness, event, ledger

from vpn_api import operations as ops
from vpn_api.db.connection import transaction
from vpn_api.db.models import Device, PeerCommand, RewardRule, Tunnel, VPNNode
from vpn_api.rewards.contracts import ClaimInput
from vpn_api.rewards.service import RewardService

__all__ = ["ledger"]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def key(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    ops.exclusive_write(path, Fernet.generate_key())
    return path


@pytest.fixture
def binaries() -> Path:
    path = Path(os.environ.get("VPN_TEST_POSTGRES_BIN", ROOT / ".cache/i-004/portable/pgsql/bin"))
    # Opt-in DB suite must actually test pg_dump/restore; absence is a failure.
    assert Path(ops.tool(path, "pg_dump")).is_file()
    assert Path(ops.tool(path, "pg_restore")).is_file()
    return path


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://operator@example.org/db",
        "postgresql://operator@localhost/db",
        "postgresql://operator@127.0.0.1/db?host=example.org",
        "postgresql://operator@127.0.0.1/db?service=production",
        "postgresql://operator@127.0.0.1/host=example.org dbname=production",
        "postgresql://operator@127.0.0.1/postgresql://example.org/db",
        "postgresql://operator@127.0.0.1/",
        "postgresql://127.0.0.1/db",
        "sqlite:///test.db",
    ],
)
def test_refuse_nonlocal_or_ambiguous_database(url: str) -> None:
    with pytest.raises(ops.OperationsError, match="local_database_required"):
        ops.local_url(url)


def test_child_environment_does_not_inherit_libpq_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGSERVICE", "untrusted")
    monkeypatch.setenv("PGPASSFILE", "untrusted")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    env = ops.pg_environment(ops.local_url("postgresql://operator@127.0.0.1/db"))
    assert not {"PGSERVICE", "PGPASSFILE", "PGHOSTADDR"}.intersection(env)
    assert env["PGHOST"] == "127.0.0.1"


def test_no_overwrite_and_bounded_read(key: Path) -> None:
    original = key.read_bytes()
    with pytest.raises(FileExistsError):
        ops.exclusive_write(key, b"replacement")
    assert key.read_bytes() == original
    with pytest.raises(ops.OperationsError, match="backup_size_limit"):
        ops.read_bounded(key, 1)


@pytest.mark.parametrize("damage", ["wrong_key", "modified", "truncated", "invalid_json"])
def test_authentication_before_any_restore(key: Path, tmp_path: Path, damage: str) -> None:
    token = Fernet(key.read_bytes()).encrypt(b"{}")
    if damage == "wrong_key":
        token = Fernet(Fernet.generate_key()).encrypt(b"private content")
    elif damage == "modified":
        token = token[:25] + (b"B" if token[25:26] == b"A" else b"A") + token[26:]
    elif damage == "truncated":
        token = token[:-10]
    archive = tmp_path / "archive.fernet"
    archive.write_bytes(token)
    with pytest.raises(ops.OperationsError, match="backup_invalid"):
        ops.load_backup(archive, key)


def test_cli_sanitizes_configuration_errors() -> None:
    env = dict(os.environ, VPN_OPERATIONS_DATABASE_URL="secret-invalid-url")
    result = subprocess.run(  # noqa: S603 - fixed local CLI with deliberately invalid config.
        [
            sys.executable,
            str(ROOT / "scripts/operations.py"),
            "check",
            "--archive",
            "missing",
            "--key-file",
            "missing",
        ],
        env=env,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == b'{"code":"operations_failed"}\r\n' or (
        result.stdout == b'{"code":"operations_failed"}\n'
    )
    assert result.stderr == b""


def add_events(h: LedgerHarness) -> None:
    h.service.apply(h.user_id, event("credit", 2**53 + 101))
    h.service.apply(h.user_id, event("debit", 17))


def drill_databases(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.scalars(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'vpn_i016_drill_%'")
            )
        )


@pytest.mark.postgres
def test_real_encrypted_backup_restore_and_cleanup(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    add_events(ledger)
    archive = tmp_path / "backup.fernet"
    value = ops.make_backup(ledger.engine, binaries, archive, key)
    assert value.tables["quota_ledger"].rows == 2
    assert not archive.read_bytes().startswith(b"PGDMP")
    assert str(ledger.user_id).encode() not in archive.read_bytes()
    assert ops.load_backup(archive, key) == value
    before = drill_databases(ledger.engine)
    # Two concurrent drills must use distinct target DBs and both remove them.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: ops.restore_drill(ledger.engine, binaries, archive, key), range(2))
        )
    assert all(r == {"tables": len(value.tables), "accounts_reconciled": 1} for r in results)
    assert drill_databases(ledger.engine) == before
    assert set(p.name for p in tmp_path.iterdir()) == {key.name, archive.name}
    with transaction(ledger.engine) as session:
        assert ledger.service.quota(session, ledger.user_id).available_bytes == 2**53 + 84


@pytest.mark.postgres
def test_concurrent_writer_does_not_split_backup_snapshot(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_events(ledger)
    original = ops.manifest

    def after_dump(conn: Connection) -> dict[str, ops.TableDigest]:
        ledger.service.apply(ledger.user_id, event("credit", 19))
        return original(conn)

    archive = tmp_path / "snapshot.fernet"
    with monkeypatch.context() as patch:
        patch.setattr(ops, "manifest", after_dump)
        backup = ops.make_backup(ledger.engine, binaries, archive, key)
    assert backup.tables["quota_ledger"].rows == 2
    assert ops.restore_drill(ledger.engine, binaries, archive, key)["accounts_reconciled"] == 1
    with ledger.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM quota_ledger")) == 3


@pytest.mark.postgres
@pytest.mark.parametrize("failure", ["manifest", "dump"])
def test_failed_restore_removes_only_its_own_database(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
    failure: str,
) -> None:
    add_events(ledger)
    archive = tmp_path / "bad.fernet"
    value = ops.make_backup(ledger.engine, binaries, archive, key)
    if failure == "manifest":
        value.tables["quota_ledger"].rows += 1
    else:
        value.dump = base64.b64encode(b"PGDMPbroken").decode()
    archive.write_bytes(Fernet(key.read_bytes()).encrypt(value.model_dump_json().encode()))
    before = drill_databases(ledger.engine)
    with pytest.raises(ops.OperationsError, match="restore_manifest_mismatch|restore_failed"):
        ops.restore_drill(ledger.engine, binaries, archive, key)
    assert drill_databases(ledger.engine) == before
    with transaction(ledger.engine) as session:
        assert ledger.service.reconcile(session, ledger.user_id).consistent


@pytest.mark.postgres
def test_dump_failure_and_size_limit_leave_no_archive(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "never.fernet"
    with monkeypatch.context() as patch:
        patch.setattr(ops, "MAX_DUMP", 1)
        with pytest.raises(ops.OperationsError, match="backup_size_limit"):
            ops.make_backup(ledger.engine, binaries, archive, key)
    assert not archive.exists()
    with pytest.raises(ops.OperationsError, match="postgres_tool_missing"):
        ops.make_backup(ledger.engine, tmp_path, archive, key)
    assert not archive.exists()


@pytest.mark.postgres
def test_monitor_detects_stale_disabled_and_missing_nodes(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    engine = ledger.engine
    now = datetime.now(UTC)
    assert ops.monitor_database(engine, now) == {"node_inventory_empty": 1}
    with transaction(engine) as session:
        for status, seen in [
            ("active", now),
            ("active", None),
            ("active", now - timedelta(seconds=90)),
            ("active", now + timedelta(seconds=1)),
            ("disabled", None),
        ]:
            session.add(
                VPNNode(
                    provisioning_key=uuid4(),
                    name="synthetic",
                    region="local",
                    status=status,
                    secret_digest="0" * 64,
                    last_seen_at=seen,
                )
            )
    assert ops.monitor_database(engine, now) == {"node_heartbeat_stale": 3}
    archive = tmp_path / "monitor.fernet"
    backup = ops.make_backup(engine, binaries, archive, key)
    now = backup.created_at
    alerts = ops.monitor(engine, archive, key, tmp_path, now=now, min_free_bytes=0)
    assert alerts == {"node_heartbeat_stale": 3}
    assert "backup_stale" not in ops.monitor(
        engine, archive, key, tmp_path, now=now + timedelta(hours=24), min_free_bytes=0
    )
    assert "backup_stale" in ops.monitor(
        engine, archive, key, tmp_path, now=now + timedelta(hours=24, seconds=1), min_free_bytes=0
    )
    assert "backup_stale" in ops.monitor(
        engine, archive, key, tmp_path, now=now - timedelta(seconds=1), min_free_bytes=0
    )
    assert "disk_space_low" in ops.monitor(
        engine, archive, key, tmp_path, now=now, min_free_bytes=2**63
    )
    backup.source = "another-source"
    archive.write_bytes(Fernet(key.read_bytes()).encrypt(backup.model_dump_json().encode()))
    assert "backup_source_mismatch" in ops.monitor(engine, archive, key, tmp_path)
    archive.unlink()
    assert "backup_unavailable" in ops.monitor(engine, archive, key, tmp_path)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
    assert "database_check_failed" in ops.monitor(engine, archive, key, tmp_path)


@pytest.mark.postgres
def test_healthy_monitor_then_node_loss_and_recovery(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    with transaction(ledger.engine) as session:
        node = VPNNode(
            provisioning_key=uuid4(),
            name="synthetic",
            region="local",
            secret_digest="0" * 64,
            last_seen_at=now,
        )
        session.add(node)
    archive = tmp_path / "healthy.fernet"
    ops.make_backup(ledger.engine, binaries, archive, key)
    assert ops.monitor(ledger.engine, archive, key, tmp_path, min_free_bytes=0) == {}
    assert ops.monitor(
        ledger.engine, archive, key, tmp_path, now=now + timedelta(seconds=90), min_free_bytes=0
    ) == {"node_heartbeat_stale": 1}
    with transaction(ledger.engine) as session:
        current = session.scalar(select(VPNNode))
        assert current is not None
        current.last_seen_at = datetime.now(UTC)
    assert ops.monitor(ledger.engine, archive, key, tmp_path, min_free_bytes=0) == {}


@pytest.mark.postgres
def test_pending_command_alert_and_ack_recovery(ledger: LedgerHarness) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(seconds=90)
    with transaction(ledger.engine) as session:
        node = VPNNode(
            provisioning_key=uuid4(),
            name="synthetic",
            region="local",
            secret_digest="0" * 64,
            last_seen_at=now,
        )
        device = Device(
            user_id=ledger.user_id,
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(os.urandom(32)).decode(),
            created_at=old,
        )
        session.add_all([node, device])
        session.flush()
        tunnel = Tunnel(
            user_id=ledger.user_id,
            device_id=device.id,
            idempotency_key=uuid4(),
            node_id=node.id,
            state="revoke_pending",
            peer_version=2,
            created_at=old,
        )
        session.add(tunnel)
        session.flush()
        command = PeerCommand(
            tunnel_id=tunnel.id,
            node_id=node.id,
            operation="remove_peer",
            peer_version=2,
            created_at=old,
        )
        session.add(command)
    assert ops.monitor_database(ledger.engine, now) == {"peer_command_stalled": 1}
    with transaction(ledger.engine) as session:
        pending = session.scalar(select(PeerCommand))
        assert pending is not None
        pending.acknowledged_at = now
    assert ops.monitor_database(ledger.engine, now) == {}


@pytest.mark.postgres
def test_reward_accounting_survives_restore(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    with transaction(ledger.engine) as session:
        rule = RewardRule(
            reward_type="daily",
            reward_bytes=17,
            daily_cap_bytes=17,
            daily_cap_claims=1,
            cooldown_seconds=1,
            enabled=True,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
        )
        session.add(rule)
        session.flush()
        body = ClaimInput(rule_id=rule.id, idempotency_key=uuid4())
        service = RewardService(ledger.engine)
        first = service.claim(session, ledger.user_id, body)
        assert service.claim(session, ledger.user_id, body) == first
    archive = tmp_path / "rewards.fernet"
    value = ops.make_backup(ledger.engine, binaries, archive, key)
    assert value.tables["reward_claims"].rows == 1
    assert value.tables["quota_ledger"].rows == 1
    assert ops.restore_drill(ledger.engine, binaries, archive, key)["accounts_reconciled"] == 1


@pytest.mark.postgres
def test_cli_backup_drill_and_unhealthy_monitor(
    ledger: LedgerHarness,
    binaries: Path,
    tmp_path: Path,
) -> None:
    add_events(ledger)
    key = tmp_path / "cli.key"
    archive = tmp_path / "cli.fernet"
    env = dict(os.environ, VPN_OPERATIONS_DATABASE_URL=ledger.engine.url.render_as_string(False))

    def run(action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - local CLI, private DB URL only in environment.
            [
                sys.executable,
                str(ROOT / "scripts/operations.py"),
                action,
                "--key-file",
                str(key),
                "--archive",
                str(archive),
                "--postgres-bin",
                str(binaries),
                "--disk",
                str(tmp_path),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

    assert run("keygen").returncode == 0
    assert run("backup").returncode == 0
    result = run("drill")
    assert result.returncode == 0 and '"drill_passed"' in result.stdout
    result = run("check")
    assert result.returncode == 1 and '"node_inventory_empty"' in result.stdout
    assert result.stderr == ""
    assert ledger.engine.url.password is not None
    assert ledger.engine.url.password not in result.stdout
    # Wrong key must fail before creating a target and must not expose errors/credentials.
    key.write_bytes(Fernet.generate_key())
    before = drill_databases(ledger.engine)
    result = run("drill")
    assert result.returncode == 2 and result.stderr == ""
    assert drill_databases(ledger.engine) == before

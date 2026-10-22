"""Real PostgreSQL, synthetic counters/key-shaped bytes; never real node/VPN evidence."""

import base64
import itertools
import json
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_migrations import migration_config
from test_quota import event

from vpn_api.config import Settings
from vpn_api.db.connection import transaction
from vpn_api.db.models import Device, QuotaOperation, UsageEpoch, UsageSample, UsageStream, User
from vpn_api.main import create_app
from vpn_api.quota.contracts import MAX_BYTES
from vpn_api.quota.service import LedgerService
from vpn_api.usage.contracts import UsageError, UsageSampleInput
from vpn_api.usage.service import UsageService

pytestmark = pytest.mark.postgres


@dataclass
class Harness:
    engine: Engine
    usage: UsageService
    user_id: UUID
    device_id: UUID
    node: UUID
    peer: UUID
    epoch: UUID
    stream: UUID
    base: datetime


@pytest.fixture
def usage(engine: Engine) -> Harness:
    command.upgrade(migration_config(engine), "head")
    with transaction(engine) as session:
        user = User(status="active")
        session.add(user)
        session.flush()
        device = Device(
            user_id=user.id,
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(secrets.token_bytes(32)).decode(),
            created_at=datetime.now(UTC),
        )
        session.add(device)
        session.flush()
        user_id, device_id = user.id, device.id
    service = UsageService(engine)
    node, peer, epoch = uuid4(), uuid4(), uuid4()
    base = datetime.now(UTC) - timedelta(minutes=5)
    stream = service.bind(node, peer, device_id)
    service.open_epoch(node, peer, epoch, 0, base)
    LedgerService(engine).apply(user_id, event("credit", 1000))
    return Harness(engine, service, user_id, device_id, node, peer, epoch, stream, base)


def report(
    h: Harness,
    seq: int,
    rx: int,
    tx: int = 0,
    *,
    epoch: UUID | None = None,
    at: datetime | None = None,
    key: UUID | None = None,
) -> UsageSampleInput:
    return UsageSampleInput(
        schema_version=1,
        peer_id=h.peer,
        counter_epoch=epoch or h.epoch,
        sequence=seq,
        sampled_at=at or h.base + timedelta(seconds=min(seq, 100)),
        rx_total_bytes=rx,
        tx_total_bytes=tx,
        idempotency_key=key or uuid4(),
    )


def used(h: Harness) -> int:
    with transaction(h.engine) as session:
        return LedgerService(h.engine).quota(session, h.user_id).used_bytes


def audit(h: Harness, pending: int = 0) -> None:
    with transaction(h.engine) as session:
        result = h.usage.audit_user(session, h.user_id)
        assert result.consistent and result.violations == 0
        assert result.pending_bytes == pending


def test_first_sample_charges_rx_plus_tx_from_zero_and_restart_uses_db(usage: Harness) -> None:
    h = usage
    assert h.usage.ingest(h.node, report(h, 1, 10, 5)).delta_bytes == 15
    restarted = UsageService(h.engine)
    assert restarted.ingest(h.node, report(h, 2, 20, 8)).delta_bytes == 13
    assert used(h) == 28
    audit(h)


def test_duplicate_key_and_payload_return_original_receipt(usage: Harness) -> None:
    h = usage
    original = report(h, 1, 10)
    result = h.usage.ingest(h.node, original)
    h.usage.ingest(h.node, report(h, 2, 20))
    assert h.usage.ingest(h.node, original) == result
    assert used(h) == 20
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(UsageSample)) == 2


@pytest.mark.parametrize("field", ["rx_total_bytes", "sampled_at", "sequence", "counter_epoch"])
def test_changed_payload_for_same_key_fails_closed(usage: Harness, field: str) -> None:
    h = usage
    original = report(h, 1, 10)
    h.usage.ingest(h.node, original)
    change = {
        "rx_total_bytes": 11,
        "sampled_at": h.base + timedelta(seconds=2),
        "sequence": 2,
        "counter_epoch": uuid4(),
    }[field]
    with pytest.raises(UsageError, match="usage_idempotency_conflict"):
        h.usage.ingest(h.node, original.model_copy(update={field: change}))
    assert used(h) == 10


def test_new_key_cannot_rebill_same_epoch_sequence(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 10))
    with pytest.raises(UsageError, match="usage_sequence_conflict"):
        h.usage.ingest(h.node, report(h, 1, 10))
    assert used(h) == 10


@pytest.mark.parametrize("order", list(itertools.permutations([1, 2, 3])))
def test_out_of_order_delivery_never_double_counts(usage: Harness, order: tuple[int, ...]) -> None:
    h = usage
    for sequence in order:
        h.usage.ingest(h.node, report(h, sequence, sequence * 10))
    assert used(h) == 30
    audit(h)


@pytest.mark.parametrize(
    "kind", ["counter", "time", "stale_counter", "stale_time", "future", "before_epoch"]
)
def test_invalid_progress_is_durable_and_does_not_move_watermark(usage: Harness, kind: str) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 2, 20))
    bad = {
        "counter": report(h, 3, 19),
        "time": report(h, 3, 21, at=h.base + timedelta(seconds=1)),
        "stale_counter": report(h, 1, 21),
        "stale_time": report(h, 1, 19, at=h.base + timedelta(seconds=3)),
        "future": report(h, 3, 21, at=datetime.now(UTC) + timedelta(minutes=2)),
        "before_epoch": report(h, 3, 21, at=h.base - timedelta(seconds=1)),
    }[kind]
    result = h.usage.ingest(h.node, bad)
    assert result.status == "rejected" and result.delta_bytes == 0
    assert h.usage.ingest(h.node, bad) == result
    assert used(h) == 20
    assert h.usage.ingest(h.node, report(h, 4, 25)).delta_bytes == 5
    audit(h)


def test_zero_and_unchanged_counters_do_not_create_fake_debits(usage: Harness) -> None:
    h = usage
    first = h.usage.ingest(h.node, report(h, 1, 0))
    assert first.status == "no_change" and first.quota_operation_id is None
    h.usage.ingest(h.node, report(h, 2, 10))
    unchanged = h.usage.ingest(h.node, report(h, 3, 10))
    assert unchanged.status == "no_change" and used(h) == 10
    with transaction(h.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_type == "usage")
            )
            == 1
        )
    audit(h)


def test_blocked_replay_stays_blocked_and_new_report_catches_up(usage: Harness) -> None:
    h = usage
    original = report(h, 1, 1200)
    blocked = h.usage.ingest(h.node, original)
    assert blocked.status == "blocked" and blocked.pending_bytes == 1200 and used(h) == 0
    LedgerService(h.engine).apply(h.user_id, event("credit", 500))
    assert h.usage.ingest(h.node, original) == blocked
    assert used(h) == 0
    caught = h.usage.ingest(h.node, report(h, 2, 1250))
    assert caught.status == "charged" and caught.delta_bytes == 1250 and caught.pending_bytes == 0
    assert used(h) == 1250
    audit(h)


def test_blocked_observation_cannot_be_regressed_to_hide_debt(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 1200))
    smaller = h.usage.ingest(h.node, report(h, 2, 1100))
    assert smaller.reason == "counter_regression" and smaller.pending_bytes == 1200
    stale = h.usage.ingest(h.node, report(h, 3, 1200))
    assert stale.status == "blocked" and stale.pending_bytes == 1200
    audit(h, 1200)


def test_reset_requires_trusted_epoch_and_late_old_epoch_is_accounted(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 10))
    with pytest.raises(UsageError, match="usage_epoch_unknown"):
        h.usage.ingest(h.node, report(h, 1, 3, epoch=uuid4()))
    next_epoch = uuid4()
    h.usage.open_epoch(h.node, h.peer, next_epoch, 1, h.base + timedelta(seconds=10))
    h.usage.ingest(h.node, report(h, 1, 3, epoch=next_epoch, at=h.base + timedelta(seconds=11)))
    late = h.usage.ingest(h.node, report(h, 2, 15, at=h.base + timedelta(seconds=5)))
    assert late.delta_bytes == 5 and used(h) == 18
    rejected = h.usage.ingest(h.node, report(h, 3, 20, at=h.base + timedelta(seconds=12)))
    assert rejected.reason == "outside_epoch" and used(h) == 18
    audit(h)


def test_new_epoch_preserves_old_pending_and_final_snapshot_settles_it(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 1200))
    next_epoch = uuid4()
    h.usage.open_epoch(h.node, h.peer, next_epoch, 1, h.base + timedelta(seconds=10))
    h.usage.ingest(h.node, report(h, 1, 5, epoch=next_epoch, at=h.base + timedelta(seconds=11)))
    audit(h, 1200)
    LedgerService(h.engine).apply(h.user_id, event("credit", 500))
    final = h.usage.ingest(h.node, report(h, 2, 1200, at=h.base + timedelta(seconds=1)))
    assert final.status == "charged" and used(h) == 1205
    audit(h)


def test_epoch_cas_and_replay_do_not_reactivate_retired_epoch(usage: Harness) -> None:
    h = usage
    newer = uuid4()
    h.usage.open_epoch(h.node, h.peer, newer, 1, h.base + timedelta(seconds=10))
    assert h.usage.open_epoch(h.node, h.peer, h.epoch, 0, h.base) == h.epoch
    with pytest.raises(UsageError, match="usage_generation_conflict"):
        h.usage.open_epoch(h.node, h.peer, uuid4(), 1, h.base + timedelta(seconds=20))
    with pytest.raises(UsageError, match="usage_epoch_conflict"):
        h.usage.open_epoch(h.node, h.peer, newer, 1, h.base + timedelta(seconds=11))
    audit(h)


def test_epoch_cannot_cut_before_already_observed_report(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 10, 10))
    with pytest.raises(UsageError, match="usage_invalid_epoch"):
        h.usage.open_epoch(h.node, h.peer, uuid4(), 1, h.base + timedelta(seconds=5))
    audit(h)


def test_unknown_node_or_foreign_epoch_cannot_move_owner_counters(usage: Harness) -> None:
    h = usage
    with pytest.raises(UsageError, match="usage_source_unknown"):
        h.usage.ingest(uuid4(), report(h, 1, 10))
    other_peer, other_epoch = uuid4(), uuid4()
    h.usage.bind(h.node, other_peer, h.device_id)
    h.usage.open_epoch(h.node, other_peer, other_epoch, 0, h.base)
    with pytest.raises(UsageError, match="usage_epoch_unknown"):
        h.usage.ingest(h.node, report(h, 1, 10, epoch=other_epoch))
    assert used(h) == 0


@pytest.mark.parametrize("kind", ["replay", "new_sequence"])
def test_concurrent_samples_have_one_cumulative_effect(usage: Harness, kind: str) -> None:
    h = usage
    samples = [report(h, 1 if kind == "replay" else index + 1, 20) for index in range(4)]
    if kind == "replay":
        samples = [samples[0]] * 4
    barrier = Barrier(4, timeout=15)

    def ingest(index: int) -> UUID:
        barrier.wait()
        return h.usage.ingest(h.node, samples[index]).sample_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(ingest, range(4)))
    assert len(set(ids)) == (1 if kind == "replay" else 4)
    assert used(h) == 20
    audit(h)


def test_concurrent_epoch_changes_cas_once(usage: Harness) -> None:
    h = usage
    barrier = Barrier(2, timeout=15)

    def start(_: int) -> str:
        barrier.wait()
        try:
            h.usage.open_epoch(h.node, h.peer, uuid4(), 1, h.base + timedelta(seconds=10))
            return "opened"
        except UsageError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(start, range(2))) == ["opened", "usage_generation_conflict"]
    audit(h)


def test_disabled_account_and_revoked_device_still_account_existing_peer(usage: Harness) -> None:
    h = usage
    with h.engine.begin() as connection:
        connection.execute(text("UPDATE users SET status = 'disabled'"))
        connection.execute(text("UPDATE devices SET revoked_at = now()"))
    assert h.usage.ingest(h.node, report(h, 1, 10)).status == "charged"
    with pytest.raises(UsageError, match="usage_owner_unavailable"):
        h.usage.open_epoch(h.node, h.peer, uuid4(), 1, h.base + timedelta(seconds=10))
    audit(h)


def test_failure_after_ledger_before_usage_receipt_is_atomic(usage: Harness) -> None:
    h = usage
    with h.engine.begin() as connection:
        connection.execute(
            text("""CREATE FUNCTION synthetic_usage_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic usage abort'; END; $$;""")
        )
        connection.execute(
            text("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON usage_samples
            FOR EACH ROW EXECUTE FUNCTION synthetic_usage_failure()""")
        )
    sample = report(h, 1, 10)
    with pytest.raises(DBAPIError, match="synthetic usage abort"):
        h.usage.ingest(h.node, sample)
    assert used(h) == 0
    with transaction(h.engine) as session:
        epoch = session.get(UsageEpoch, h.epoch)
        assert epoch is not None and epoch.last_sequence == epoch.receipt_count == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_type == "usage")
            )
            == 0
        )
    with h.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER synthetic_failure ON usage_samples"))
        connection.execute(text("DROP FUNCTION synthetic_usage_failure()"))
    assert h.usage.ingest(h.node, sample).delta_bytes == 10
    audit(h)


@pytest.mark.parametrize(
    "action",
    [
        "UPDATE usage_samples SET status=status",
        "DELETE FROM usage_samples",
        "TRUNCATE usage_samples",
    ],
)
def test_usage_receipts_are_immutable(usage: Harness, action: str) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 10))
    with pytest.raises(DBAPIError, match="append only"), h.engine.begin() as connection:
        connection.execute(text(action))
    audit(h)


def test_tampered_projection_is_detected_and_ingest_fails_closed(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 10))
    with h.engine.begin() as connection:
        connection.execute(text("UPDATE usage_epochs SET rx_bytes = 11"))
    with pytest.raises(UsageError, match="usage_inconsistent"):
        h.usage.ingest(h.node, report(h, 2, 20))
    with transaction(h.engine) as session:
        result = h.usage.audit_user(session, h.user_id)
        assert not result.consistent and result.violations > 0
    assert used(h) == 10


def test_binding_is_stable_and_cannot_be_remapped(usage: Harness) -> None:
    h = usage
    assert h.usage.bind(h.node, h.peer, h.device_id) == h.stream
    with transaction(h.engine) as session:
        device = Device(
            user_id=h.user_id,
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(secrets.token_bytes(32)).decode(),
            created_at=datetime.now(UTC),
        )
        session.add(device)
        session.flush()
        other_device = device.id
    with pytest.raises(UsageError, match="usage_binding_conflict"):
        h.usage.bind(h.node, h.peer, other_device)


def test_schema_blocks_cross_owner_binding(usage: Harness) -> None:
    h = usage
    with transaction(h.engine) as session:
        other = User(status="active")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(IntegrityError) as failure, transaction(h.engine) as session:
        session.add(
            UsageStream(
                node_id=uuid4(),
                peer_id=uuid4(),
                device_id=h.device_id,
                user_id=other_id,
                created_at=datetime.now(UTC),
            )
        )
    assert (
        getattr(getattr(failure.value.orig, "diag", None), "constraint_name", None)
        == "fk_usage_streams_device_owner"
    )


def test_big_integer_counter_precision_and_combined_overflow(usage: Harness) -> None:
    h = usage
    amount = 2**53 + 17
    LedgerService(h.engine).apply(h.user_id, event("credit", amount))
    sample = report(h, 1, amount)
    parsed = UsageSampleInput.model_validate_json(sample.model_dump_json())
    assert h.usage.ingest(h.node, parsed).delta_bytes == amount
    assert used(h) == amount
    with pytest.raises(ValidationError):
        report(h, 2, MAX_BYTES, 1)
    audit(h)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sequence", True),
        ("sequence", 1.0),
        ("sequence", "1"),
        ("rx_total_bytes", -1),
        ("rx_total_bytes", 1.5),
        ("tx_total_bytes", True),
        ("schema_version", True),
        ("schema_version", 2),
        ("sampled_at", "2026-10-18T12:00:00"),
        ("sampled_at", 1),
        ("user_id", "untrusted"),
        ("node_id", "untrusted"),
        ("url", "https://example.invalid"),
    ],
)
def test_sample_contract_rejects_untrusted_or_inexact_fields(field: str, value: object) -> None:
    data: dict[str, object] = {
        "schema_version": 1,
        "peer_id": uuid4(),
        "counter_epoch": uuid4(),
        "sequence": 1,
        "sampled_at": datetime.now(UTC),
        "rx_total_bytes": 0,
        "tx_total_bytes": 0,
        "idempotency_key": uuid4(),
    }
    data[field] = value
    with pytest.raises(ValidationError):
        UsageSampleInput.model_validate(data)


def test_no_public_usage_or_epoch_mutation_routes() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        for path in ("/api/v1/usage", "/api/v1/usage/samples", "/api/v1/usage/epochs"):
            assert client.post(path, json={"rx_total_bytes": 1}).status_code == 404


def test_nonempty_usage_downgrade_refuses_without_changing_state(usage: Harness) -> None:
    h = usage
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0004_quota_ledger")
    assert h.usage.ingest(h.node, report(h, 1, 10)).status == "charged"
    audit(h)


def test_cli_ingest_restart_and_audit_use_same_real_database(
    usage: Harness, tmp_path: Path
) -> None:
    h = usage
    path = tmp_path / "synthetic-report.json"
    sample = report(h, 1, 10)
    path.write_text(sample.model_dump_json(), encoding="utf-8")
    env = dict(os.environ)
    env.update(
        VPN_DATABASE_URL=h.engine.url.render_as_string(False),
        VPN_ENABLE_AUTH="false",
        VPN_ENVIRONMENT="test",
    )

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed local tool, ephemeral DB via env, no shell
            [sys.executable, "scripts/usage_local.py", *arguments],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    arguments = ["ingest", "--node-id", str(h.node), "--sample-file", str(path)]
    first, replay = run(arguments), run(arguments)
    assert first.returncode == replay.returncode == 0
    assert json.loads(first.stdout) == json.loads(replay.stdout)
    result = run(["audit"])
    assert result.returncode == 0
    counts = json.loads(result.stdout)
    assert counts["samples"] == 1 and counts["pending_bytes"] == 0 and counts["violations"] == 0
    assert str(h.user_id) not in result.stdout
    assert h.engine.url.render_as_string(False) not in result.stdout + result.stderr
    assert used(h) == 10

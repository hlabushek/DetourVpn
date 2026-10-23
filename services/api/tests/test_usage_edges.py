"""Additional real-PostgreSQL cross-stream/epoch ordering tests, synthetic inputs only."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from test_migrations import migration_config
from test_quota import event
from test_usage import Harness, audit, report, usage, used

from vpn_api.db.connection import transaction
from vpn_api.db.models import QuotaOperation, UsageSample, User
from vpn_api.quota.service import LedgerService
from vpn_api.usage.contracts import UsageError

__all__ = ["usage"]
pytestmark = pytest.mark.postgres


def test_directional_counter_regression_is_rejected_even_if_total_increases(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 20))
    result = h.usage.ingest(h.node, report(h, 2, 19, 10))
    assert result.reason == "counter_regression" and used(h) == 20
    audit(h)


def test_rejected_receipts_remain_auditable_after_later_epoch_retirement(usage: Harness) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 10))
    bad = report(h, 2, 9, at=h.base + timedelta(seconds=20))
    refused = h.usage.ingest(h.node, bad)
    assert refused.reason == "counter_regression"
    h.usage.open_epoch(h.node, h.peer, uuid4(), 1, h.base + timedelta(seconds=10))
    assert h.usage.ingest(h.node, bad) == refused
    audit(h)


def test_same_node_key_cannot_be_reused_for_other_peer(usage: Harness) -> None:
    h = usage
    sample = report(h, 1, 10)
    h.usage.ingest(h.node, sample)
    peer, epoch = uuid4(), uuid4()
    h.usage.bind(h.node, peer, h.device_id)
    h.usage.open_epoch(h.node, peer, epoch, 0, h.base)
    with pytest.raises(UsageError, match="usage_idempotency_conflict"):
        h.usage.ingest(h.node, sample.model_copy(update={"peer_id": peer, "counter_epoch": epoch}))
    assert used(h) == 10


def test_concurrent_peers_share_one_quota_admission_lock(usage: Harness) -> None:
    h = usage
    peer, epoch = uuid4(), uuid4()
    h.usage.bind(h.node, peer, h.device_id)
    h.usage.open_epoch(h.node, peer, epoch, 0, h.base)
    reports = [
        report(h, 1, 800),
        report(h, 1, 800).model_copy(update={"peer_id": peer, "counter_epoch": epoch}),
    ]
    barrier = Barrier(2, timeout=15)

    def ingest(index: int) -> str:
        barrier.wait()
        return h.usage.ingest(h.node, reports[index]).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(ingest, range(2))) == ["blocked", "charged"]
    assert used(h) == 800
    audit(h, 800)


def test_epoch_rotation_and_old_report_race_remain_consistent(usage: Harness) -> None:
    h = usage
    barrier = Barrier(2, timeout=15)

    def rotate() -> str:
        barrier.wait()
        try:
            h.usage.open_epoch(h.node, h.peer, uuid4(), 1, h.base + timedelta(seconds=10))
            return "opened"
        except UsageError as error:
            return error.code

    def ingest() -> str:
        barrier.wait()
        return h.usage.ingest(h.node, report(h, 1, 10, at=h.base + timedelta(seconds=15))).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        opened, accepted = pool.submit(rotate), pool.submit(ingest)
        outcomes = opened.result(timeout=15), accepted.result(timeout=15)
    assert outcomes in {("opened", "rejected"), ("usage_invalid_epoch", "charged")}
    audit(h)


def test_outer_transaction_rollback_discards_all_accounting(usage: Harness) -> None:
    h = usage
    with (
        pytest.raises(RuntimeError, match="synthetic outer abort"),
        transaction(h.engine) as session,
    ):
        h.usage.ingest_in_transaction(session, h.node, report(h, 1, 10))
        raise RuntimeError("synthetic outer abort")
    assert used(h) == 0
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(UsageSample)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_type == "usage")
            )
            == 0
        )
    audit(h)


def test_funding_prevents_legacy_usage_downgrade_and_preserves_quota(engine: Engine) -> None:
    config = migration_config(engine)
    command.upgrade(config, "head")
    with transaction(engine) as session:
        user = User(status="active")
        session.add(user)
        session.flush()
        user_id = user.id
    LedgerService(engine).apply(user_id, event("credit", 10))
    # Since 0012, even an unused credit has funding provenance. Removing it is
    # intentionally forbidden; the failed downgrade must preserve the whole schema.
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(config, "0004_quota_ledger")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT to_regprocedure('reject_usage_mutation()')")) is not None
        )
    command.upgrade(config, "head")
    command.check(config)
    with transaction(engine) as session:
        assert LedgerService(engine).quota(session, user_id).available_bytes == 10


def test_cli_audit_detects_corruption_and_invalid_input_is_not_echoed(
    usage: Harness, tmp_path: Path
) -> None:
    h = usage
    h.usage.ingest(h.node, report(h, 1, 10))
    with h.engine.begin() as connection:
        connection.execute(text("UPDATE usage_epochs SET rx_bytes = 11"))
    env = dict(os.environ)
    env.update(
        VPN_DATABASE_URL=h.engine.url.render_as_string(False),
        VPN_ENABLE_AUTH="false",
        VPN_ENVIRONMENT="test",
    )

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed workspace script, no shell
            [sys.executable, "scripts/usage_local.py", *arguments],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    failed = run(["audit"])
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["violations"] > 0
    assert str(h.user_id) not in failed.stdout
    marker = "synthetic-private-input-must-not-be-echoed"
    path = tmp_path / "invalid-sample.json"
    path.write_text(json.dumps({"untrusted_extra": marker}), encoding="utf-8")
    rejected = run(["ingest", "--node-id", str(h.node), "--sample-file", str(path)])
    assert rejected.returncode == 2
    assert marker not in rejected.stdout + rejected.stderr
    path.write_bytes(b"x" * 16385)
    too_large = run(["ingest", "--node-id", str(h.node), "--sample-file", str(path)])
    assert too_large.returncode == 2 and "usage_sample_too_large" in too_large.stdout

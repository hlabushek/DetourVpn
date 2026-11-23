"""TEST ONLY (H4, ADR-179): a gate that never opened releases the budget; an open gate charges.

Node side: simulated gate/peer with a real SQLite journal. Server side: real PostgreSQL.
No VPN, kernel or network.
"""

import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, harness
from test_migrations import HEAD, migration_config
from test_window_funding import close
from test_window_runtime import TestGate, setup, value
from test_window_service import BUDGET, boot, create, inventory, poll, request

from vpn_api.db.connection import transaction
from vpn_api.db.models import PeerConnection, PeerWindowReceipt
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.v3_contracts import ContractError, WindowGrant
from vpn_api.tunnels.window_journal import RuntimeFault
from vpn_api.tunnels.window_runtime import WindowJournal, WindowRuntime

__all__ = ["harness"]


def test_revoke_of_a_never_dispatched_grant_is_not_opened(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    pending = value(runtime, clock)
    runtime.close_grant(pending)  # e.g. the user disconnected before the node applied it
    receipt = runtime.journal.receipt(pending.window_id)
    assert receipt is not None and receipt.observation == "not_opened"
    assert receipt.observed_bytes is None and gate.opens == []
    runtime.journal.db.close()


def test_clock_skewed_grant_is_closed_as_not_opened(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    ahead = value(runtime, clock)
    ahead = WindowGrant.model_validate(
        {
            **ahead.model_dump(),
            "issued_at": clock.now + timedelta(seconds=5),
            "not_after": clock.now + timedelta(seconds=95),
        }
    )
    with pytest.raises(ContractError, match="grant_clock_skew"):
        runtime.apply(ahead)
    runtime.close_grant(ahead)  # what the daemon does for a declined grant
    receipt = runtime.journal.receipt(ahead.window_id)
    assert receipt is not None and receipt.observation == "not_opened" and gate.opens == []
    runtime.journal.db.close()


def test_dispatch_deadline_failure_before_open_is_not_opened(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    short = value(runtime, clock)
    short = WindowGrant.model_validate(
        {**short.model_dump(), "not_after": clock.now + timedelta(seconds=5)}
    )
    with pytest.raises(RuntimeFault, match="dispatch_deadline_exhausted"):
        runtime.apply(short)
    receipt = runtime.journal.receipt(short.window_id)
    assert receipt is not None and receipt.observation == "not_opened" and gate.opens == []
    runtime.journal.db.close()


def test_node_restart_during_intent_before_open_is_not_opened(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    runtime, gate, peer, clock = setup(path)
    first = value(runtime, clock)

    def power_loss(_budget: int) -> None:
        raise SystemExit("TEST ONLY power loss before the gate opened")

    gate.install_closed = power_loss  # type: ignore[method-assign, assignment]
    with pytest.raises(SystemExit):
        runtime.apply(first)
    runtime.journal.db.close()
    fresh = TestGate()
    fresh.exists = True
    restarted = WindowRuntime(
        runtime.node_id,
        WindowJournal(sqlite3.connect(path)),
        {1: fresh},
        {1: peer},
        now=lambda: clock.now,
    )
    restarted.startup()
    [receipt] = restarted.journal.pending()
    assert receipt.window_id == first.window_id and receipt.observation == "not_opened"
    restarted.journal.db.close()


def test_an_opened_gate_is_still_measured_and_recovery_stays_unknown(tmp_path: Path) -> None:
    runtime, gate, _, clock = setup(tmp_path / "journal.db")
    first = value(runtime, clock)
    runtime.apply(first)
    gate.amount = 777
    runtime.close_grant(first)
    receipt = runtime.journal.receipt(first.window_id)
    assert receipt is not None and (receipt.observation, receipt.observed_bytes) == (
        "measured",
        777,
    )
    runtime.journal.db.close()
    # Recovery of an older runtime's grant cannot prove the gate never opened (ADR-179).
    other, _, _, other_clock = setup(tmp_path / "other.db")
    old = value(other, other_clock).model_copy(update={"runtime_id": uuid4()})
    assert other.recover(old).observation == "unknown"
    other.journal.db.close()


@pytest.mark.postgres
def test_not_opened_receipt_releases_the_hold_and_charges_nothing(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h, slots=2)
    runtime = boot(h, node, selected)
    released_user, charged_user = request(h, selected, 711), request(h, selected, 722)
    released_id, released_grant = create(h, selected, released_user)
    charged_id, charged_grant = create(h, selected, charged_user)
    poll(h, node, selected, runtime)
    never = close(released_grant, None).model_copy(update={"observation": "not_opened"})
    opened = close(charged_grant, 4096)
    result = poll(h, node, selected, runtime, receipts=[never, opened])
    assert result.acknowledged_receipts == [never, opened]
    with transaction(h.engine) as session:
        ledger = LedgerService(h.engine)
        released = ledger._state(session, released_user.user_id)
        charged = ledger._state(session, charged_user.user_id)
        assert (released.used_bytes, released.reserved_bytes) == (0, 0)
        assert released.available_bytes == BUDGET * 5  # the whole credit is free again
        assert (charged.used_bytes, charged.reserved_bytes) == (4096, 0)
        row = session.get(PeerWindowReceipt, released_grant.window_id)
        assert row is not None and row.observation == "not_opened"  # kept for audit
        assert row.operation_id is None and row.observed_bytes is None
        for connection_id in (released_id, charged_id):
            connection = session.get(PeerConnection, connection_id)
            assert connection is not None and connection.state == "closed"
        for user in (released_user, charged_user):
            assert ledger.reconcile(session, user.user_id).consistent
    # The migration refuses to roll back while a not_opened receipt exists.
    config = migration_config(h.engine)
    with pytest.raises(DBAPIError, match="not_opened receipts exist"):
        command.downgrade(config, "0027_panel_server_sources")
    with transaction(h.engine) as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == HEAD

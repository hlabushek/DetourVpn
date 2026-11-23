"""TEST ONLY (M2, ADR-173): one connection's bad data does not stop the node's poll.

Real PostgreSQL; synthetic node acknowledgements; no VPN.
"""

import logging

import pytest
from sqlalchemy import func, select, text
from test_authentication import Harness, harness
from test_window_funding import close
from test_window_service import BUDGET, boot, create, inventory, poll, request

from vpn_api.db.connection import transaction
from vpn_api.db.models import PeerConnection, PeerWindowReceipt

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def test_bad_receipt_and_inconsistent_connection_are_skipped_alone(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = harness
    node, selected = inventory(h, slots=3)
    runtime = boot(h, node, selected)
    first, second, third = (request(h, selected, subject) for subject in (311, 322, 333))
    first_id, first_grant = create(h, selected, first)
    second_id, second_grant = create(h, selected, second)
    third_id, third_grant = create(h, selected, third)
    active = [first_grant.window_id, second_grant.window_id, third_grant.window_id]
    poll(h, node, selected, runtime)
    poll(h, node, selected, runtime, active=active)
    # TEST ONLY corruption: the first connection no longer matches its funding hold.
    with transaction(h.engine) as session:
        session.execute(
            text("UPDATE peer_connections SET generation = generation + 1 WHERE id = :id"),
            {"id": first_id},
        )
    bad = close(second_grant, BUDGET + 1)  # more than the budget: a contract violation
    good = close(third_grant, 10)
    caplog.set_level(logging.WARNING, logger="vpn_api.windows")

    result = poll(h, node, selected, runtime, active=active, receipts=[bad, good])

    assert result.acknowledged_receipts == [good]
    assert result.commands == []
    codes = {record.getMessage() for record in caplog.records}
    assert "window_poll_item_failed code=window_inconsistent" in codes
    assert "window_poll_item_failed code=kernel_budget_violation" in codes
    with transaction(h.engine) as session:
        states = {
            row.id: row.state
            for row in session.scalars(
                select(PeerConnection).where(PeerConnection.id.in_([first_id, second_id, third_id]))
            )
        }
        receipts = session.scalar(select(func.count()).select_from(PeerWindowReceipt))
    # The good receipt settled; the bad one left nothing behind; the corrupt one is untouched.
    assert states == {first_id: "active", second_id: "active", third_id: "closed"}
    assert receipts == 1


def test_foreign_receipt_is_refused_without_failing_the_poll(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    _, grant = create(h, selected, request(h, selected, 344))
    foreign_node, foreign_manifest = inventory(h)
    foreign_runtime = boot(h, foreign_node, foreign_manifest)
    result = poll(h, foreign_node, foreign_manifest, foreign_runtime, receipts=[close(grant, 123)])
    assert result.acknowledged_receipts == [] and result.commands == []
    assert poll(h, node, selected, runtime).commands[0].grant == grant

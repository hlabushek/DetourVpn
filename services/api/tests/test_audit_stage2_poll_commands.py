"""TEST ONLY audit stage 2 (C2, ADR-168): a node poll with more than 32 due commands.

Before ADR-168 poll_windows built one command per due connection without a cap and
WindowCommands allows at most 32, so the whole poll raised. Real PostgreSQL, synthetic
node, no VPN.
"""

from uuid import uuid4

import pytest
from test_authentication import Harness, harness
from test_window_service import create, inventory, poll, request, verify

from vpn_api.db.connection import transaction
from vpn_api.db.models import PeerConnection
from vpn_api.tunnels.v3_contracts import POOL_CAPABILITIES
from vpn_api.tunnels.window_service import EpochStart, start_epoch

__all__ = ["harness"]
pytestmark = pytest.mark.postgres
PEERS = 33


def test_poll_with_more_than_32_due_connections_still_answers(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    # C1 would rate limit 33 logins from one test client; it is not under test here.
    monkeypatch.setattr(h.auth, "rate", lambda *_args, **_kwargs: None)
    node, physical = inventory(h, slots=1)
    selected = physical.model_copy(update={"pool_peers": PEERS})
    runtime = uuid4()
    with transaction(h.engine) as session:
        verify(h, session, node)
        start_epoch(
            session,
            h.auth,
            selected,
            EpochStart(
                contract_version=3,
                runtime_id=runtime,
                previous_runtime_id=None,
                capabilities=sorted(POOL_CAPABILITIES),
                server_public_keys=[s.server_public_key for s in selected.slots],
                gates_closed=True,
            ),
        )
    for subject in range(5000, 5000 + PEERS):
        create(h, selected, request(h, selected, subject))
    # 33 pending connections are due. The poll must answer (at most 32 now, the rest later).
    result = poll(h, node, selected, runtime)
    assert len(result.commands) == 32
    deadlines = [command.grant.not_after for command in result.commands]
    assert deadlines == sorted(deadlines)  # nearest deadline first
    # The node applies the 32; the remaining window follows in the next poll.
    active = [command.grant.window_id for command in result.commands]
    rest = poll(h, node, selected, runtime, active=active)
    assert len(rest.commands) == 1 and rest.commands[0].operation == "window"
    assert rest.commands[0].grant.window_id not in active


def test_poll_sends_revokes_before_windows(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    monkeypatch.setattr(h.auth, "rate", lambda *_args, **_kwargs: None)
    node, physical = inventory(h, slots=1)
    selected = physical.model_copy(update={"pool_peers": PEERS})
    runtime = uuid4()
    with transaction(h.engine) as session:
        verify(h, session, node)
        start_epoch(
            session,
            h.auth,
            selected,
            EpochStart(
                contract_version=3,
                runtime_id=runtime,
                previous_runtime_id=None,
                capabilities=sorted(POOL_CAPABILITIES),
                server_public_keys=[s.server_public_key for s in selected.slots],
                gates_closed=True,
            ),
        )
    created = [create(h, selected, request(h, selected, s)) for s in range(6000, 6000 + PEERS)]
    # The last-created connection is revoked by its owner while 32 others are still pending.
    with transaction(h.engine) as session:
        last = session.get(PeerConnection, created[-1][0])
        assert last is not None
        last.state = "revoke_pending"
    result = poll(h, node, selected, runtime)
    assert len(result.commands) == 32
    assert result.commands[0].operation == "revoke"
    assert result.commands[0].grant.window_id == created[-1][1].window_id

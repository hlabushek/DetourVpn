"""TEST ONLY (L3, L4, L5 — ADR-183).

L3 and L4 use a real PostgreSQL; L5 uses the simulated gate/peer and a real SQLite journal.
"""

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from test_authentication import Harness, harness, headers, login
from test_window_daemon import SyntheticControl, daemon
from test_window_runtime import next_window, setup, value
from test_window_service import boot, create, inventory, request

from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.tunnels.v3_contracts import WindowCommand, WindowCommands
from vpn_api.tunnels.window_service import issue_renew_token, token_connection

__all__ = ["harness", "daemon"]


@pytest.mark.postgres
def test_first_use_is_marked_before_the_request_locks_the_user(harness: Harness) -> None:
    """L3: the second pooled connection never waits while this request holds a row lock."""
    h = harness
    tokens = login(h, 9201)
    order: list[str] = []

    def statement(_conn: Any, _cursor: Any, sql: str, *_args: Any) -> None:
        text_ = " ".join(sql.split()).lower()
        if text_.startswith("update session_credentials") and "first_used_at" in text_:
            order.append("mark_used")
        if text_.startswith("select") and "from users" in text_ and "for update" in text_:
            order.append("user_lock")

    event.listen(h.engine, "before_cursor_execute", statement)
    try:
        assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 200
    finally:
        event.remove(h.engine, "before_cursor_execute", statement)
    assert order.index("mark_used") < order.index("user_lock")
    with h.engine.connect() as conn:
        used = conn.scalar(
            text("SELECT count(*) FROM session_credentials WHERE first_used_at IS NOT NULL")
        )
    assert used and used >= 1


@pytest.mark.postgres
def test_invalid_renew_token_is_refused_without_waiting_for_the_owner_lock(
    harness: Harness,
) -> None:
    """L4: a wrong token never queues on the owner's row; a valid one still takes the lock."""
    h = harness
    node, selected = inventory(h)
    boot(h, node, selected)
    user = request(h, selected, 9202)
    connection_id, _ = create(h, selected, user)
    with transaction(h.engine) as session:
        token = issue_renew_token(session, h.auth, user.tokens.access_token, connection_id)
    with transaction(h.engine) as holder:
        holder.execute(text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": user.user_id})
        with transaction(h.engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(AuthError, match="renew_token_invalid"):
                token_connection(session, h.auth, connection_id, "x" * 43)
        with transaction(h.engine) as session, pytest.raises(OperationalError):
            session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            token_connection(session, h.auth, connection_id, token)  # waits: lock order kept
    with transaction(h.engine) as session:
        assert token_connection(session, h.auth, connection_id, token).id == connection_id


def test_receipt_backlog_defers_the_grant_instead_of_closing_it(
    daemon: ModuleType, tmp_path: Path
) -> None:
    """L5: receipt_ack_required leaves the connection untouched; it applies after the ack."""
    runtime, gate, _, clock = setup(tmp_path / "state.sqlite")
    first = value(runtime, clock)
    runtime.apply(first)
    second = next_window(first, clock)
    runtime.apply(second)  # finishes the first window: its receipt is pending
    third = next_window(second, clock)

    def commands(*items: WindowCommand, acks: list[Any] | None = None) -> dict[str, Any]:
        return WindowCommands(
            runtime_id=runtime.runtime_id,
            ready=True,
            commands=list(items),
            acknowledged_receipts=acks or [],
        ).model_dump(mode="json")

    daemon.tick(
        runtime, SyntheticControl([commands(WindowCommand(operation="window", grant=third))])
    )
    assert not runtime.journal.fenced(third.connection_id) and gate.live
    assert runtime.journal.receipt(third.window_id) is None
    pending = runtime.journal.pending()
    daemon.tick(
        runtime,
        SyntheticControl([commands(WindowCommand(operation="window", grant=third), acks=pending)]),
    )
    current = runtime.journal.current(third.slot_id)
    assert current is not None and current[0].window_id == third.window_id
    assert current[1] == "active"
    runtime.journal.db.close()

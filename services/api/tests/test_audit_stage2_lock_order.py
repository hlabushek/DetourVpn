"""TEST ONLY audit stage 2 (L1, ADR-167 amendment): authenticate() holds the user row lock.

It confirms that every owner route locked the user row before any connection row even
before ADR-167. Real PostgreSQL, no VPN.
"""

import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from test_authentication import Harness, harness, login

from vpn_api.db.connection import transaction

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def test_authenticate_locks_the_user_row_for_the_whole_transaction(harness: Harness) -> None:
    h = harness
    tokens = login(h, 7007)
    with transaction(h.engine) as session:
        principal = h.auth.authenticate(session, tokens.access_token, "tunnels:manage")
        with h.engine.connect() as other, pytest.raises(OperationalError) as blocked:
            other.execute(
                text("SELECT id FROM users WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": principal.user_id},
            )
        assert isinstance(blocked.value.orig, LockNotAvailable)

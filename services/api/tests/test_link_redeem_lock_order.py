"""TEST ONLY (L2, ADR-182): link-code redeem checks the beta invite before locking the user.

Real PostgreSQL. The statement order is recorded from the engine; no timing assumptions.
"""

from typing import Any

import pytest
from sqlalchemy import event
from test_authentication import Harness, harness, login
from test_device_links import create_code, redeem, register

from vpn_api.auth.service import AuthService

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def test_redeem_takes_the_invite_before_the_user_row(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    owner = login(h, 9101)
    register(h, owner)
    code = str(create_code(h, owner)["code"])
    order: list[str] = []
    original = AuthService.beta_subject_allowed

    def recorded(self: AuthService, session: Any, subject: int) -> bool:
        order.append("invite")
        return original(self, session, subject)

    def statement(_conn: Any, _cursor: Any, sql: str, *_args: Any) -> None:
        text = " ".join(sql.split()).lower()
        if text.startswith("select") and "from users" in text and "for update" in text:
            order.append("user")
        if "from device_link_codes" in text and "for update" in text:
            order.append("code")

    monkeypatch.setattr(AuthService, "beta_subject_allowed", recorded)
    event.listen(h.engine, "before_cursor_execute", statement)
    try:
        response = redeem(h, code)
    finally:
        event.remove(h.engine, "before_cursor_execute", statement)
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert order[:3] == ["invite", "user", "code"]

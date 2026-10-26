"""Admin MFA containment tests use only synthetic local identities and codes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from test_admin import Harness, admin, authorization

from vpn_api.admin import AdminService
from vpn_api.admin_recovery import contain_admin_identity
from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.db.models import AdminAction, AdminIdentity, AdminSession

__all__ = ["admin"]


def test_containment_disables_identity_revokes_session_and_audits(admin: Harness) -> None:
    h = admin
    access = authorization(h)["Authorization"].removeprefix("Bearer ")

    result = contain_admin_identity(h.engine, "test-admin")

    assert result == {
        "disabled": True,
        "changed": True,
        "sessions_revoked": 1,
        "audit_recorded": True,
    }
    with transaction(h.engine) as session:
        identity = session.get(AdminIdentity, h.identity_id)
        admin_session = session.scalar(select(AdminSession))
        action = session.scalar(
            select(AdminAction).where(AdminAction.operation == "admin_mfa_containment")
        )
        assert identity is not None and identity.disabled_at is not None
        assert admin_session is not None and admin_session.revoked_at is not None
        assert action is not None and action.admin_id == identity.id

    with transaction(h.engine) as session, pytest.raises(AuthError, match="admin_unauthorized"):
        AdminService.authenticate(session, access)


def test_containment_is_idempotent_under_concurrent_retries(admin: Harness) -> None:
    h = admin
    authorization(h)
    barrier = Barrier(2, timeout=10)

    def disable() -> dict[str, object]:
        barrier.wait()
        return contain_admin_identity(h.engine, "test-admin")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: disable(), range(2)))

    assert sum(result["changed"] is True for result in results) == 1
    assert all(result["disabled"] is True for result in results)
    with h.engine.connect() as connection:
        count = connection.scalar(
            select(func.count())
            .select_from(AdminAction)
            .where(AdminAction.operation == "admin_mfa_containment")
        )
    assert count == 1


def test_containment_rejects_unknown_identity_without_changes(admin: Harness) -> None:
    h = admin
    with pytest.raises(ValueError, match="admin_identity_missing"):
        contain_admin_identity(h.engine, "missing-admin")
    with transaction(h.engine) as session:
        identity = session.get(AdminIdentity, h.identity_id)
        assert identity is not None and identity.disabled_at is None


def test_containment_rejects_invalid_username(admin: Harness) -> None:
    with pytest.raises(ValueError, match="invalid_admin_username"):
        contain_admin_identity(admin.engine, "../owner")

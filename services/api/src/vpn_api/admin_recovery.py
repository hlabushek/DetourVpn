"""Audited emergency containment for an administrator MFA re-enrollment."""

import hashlib
import re
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import AdminAction, AdminIdentity, AdminSession


def contain_admin_identity(engine: Engine, username: str) -> dict[str, object]:
    """Disable one admin and revoke sessions atomically; never delete its audit identity."""
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", username):
        raise ValueError("invalid_admin_username")
    with transaction(engine) as session:
        identity = session.scalar(
            select(AdminIdentity).where(AdminIdentity.username == username).with_for_update()
        )
        if identity is None:
            raise ValueError("admin_identity_missing")
        now = database_now(session)
        was_enabled = identity.disabled_at is None
        identity.disabled_at = identity.disabled_at or now
        active_session_ids = list(
            session.scalars(
                select(AdminSession.id)
                .where(AdminSession.admin_id == identity.id, AdminSession.revoked_at.is_(None))
                .with_for_update()
            )
        )
        revoked = len(active_session_ids)
        if active_session_ids:
            session.execute(
                update(AdminSession)
                .where(AdminSession.id.in_(active_session_ids))
                .values(revoked_at=now)
            )
        changed = was_enabled or revoked > 0
        if changed:
            session.add(
                AdminAction(
                    admin_id=identity.id,
                    operation="admin_mfa_containment",
                    reason="Operator containment; MFA re-enrollment required",
                    idempotency_key=uuid4(),
                    request_digest=hashlib.sha256(
                        f"admin-mfa-containment:{identity.id}".encode("ascii")
                    ).hexdigest(),
                    result={
                        "disabled": True,
                        "sessions_revoked": revoked,
                        "reenrollment_required": True,
                    },
                    created_at=now,
                )
            )
        return {
            "disabled": identity.disabled_at is not None,
            "changed": changed,
            "sessions_revoked": revoked,
            "audit_recorded": changed,
        }

"""Self-service account deletion (ADR-091): tombstone, not row removal.

The quota ledger, receipts and reward rows are immutable accounting records that reference
users with RESTRICT. They hold only random ids and integer bytes. Deletion therefore removes
the Telegram identity link, revokes every device, tunnel and session, and marks the user row
as a tombstone. It never deletes accounting rows.
"""

from typing import Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, select, update

from vpn_api.auth.contracts import Input
from vpn_api.auth.routes import Access, Service, network
from vpn_api.auth.security import beta_invite_digest
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    AuthIdentity,
    BetaInvite,
    Device,
    DeviceLinkCode,
    LoginAttempt,
    SessionFamily,
    User,
)
from vpn_api.schemas import ErrorResponse
from vpn_api.tunnels.service import revoke_device_tunnels

CONFIRMATION = "delete-my-account"

router = APIRouter(
    prefix="/api/v1/me",
    tags=["account"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 413, 422, 429, 503)},
)


class DeleteAccountInput(Input):
    confirm: Literal["delete-my-account"]


def delete_account(auth: AuthService, access: str) -> None:
    with transaction(auth.engine) as session:
        # authenticate() locks the user row, which serializes against other lifecycle writes.
        principal = auth.authenticate(session, access, "account:delete")
        user_id = principal.user_id
        now = database_now(session)
        devices = session.scalars(
            select(Device).where(Device.user_id == user_id).order_by(Device.id).with_for_update()
        ).all()
        for device in devices:
            if device.revoked_at is None:
                device.revoked_at = now
            revoke_device_tunnels(session, user_id, device.id)
        session.execute(
            update(SessionFamily)
            .where(SessionFamily.user_id == user_id, SessionFamily.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        if auth.settings.auth_pepper is not None:
            # A returning subject needs a fresh admin invite, so deletion cannot be used to
            # farm a new initial grant.
            session.execute(
                update(BetaInvite)
                .where(
                    BetaInvite.subject_digest
                    == beta_invite_digest(
                        auth.settings.auth_pepper.get_secret_value(), principal.telegram_subject
                    ),
                    BetaInvite.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        session.execute(
            update(DeviceLinkCode)
            .where(DeviceLinkCode.user_id == user_id, DeviceLinkCode.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        session.execute(
            delete(LoginAttempt).where(LoginAttempt.subject == principal.telegram_subject)
        )
        session.execute(delete(AuthIdentity).where(AuthIdentity.user_id == user_id))
        user = session.get(User, user_id)
        assert user is not None  # noqa: S101 - locked by authenticate() in this transaction.
        user.status = "deleted"
        user.deleted_at = now


@router.post("/delete", status_code=204)
def delete_me(
    body: DeleteAccountInput, request: Request, auth: Service, access: Access
) -> Response:
    del body  # The literal confirmation is the validation.
    auth.rate("device", network(request))
    delete_account(auth, access)
    return Response(status_code=204)

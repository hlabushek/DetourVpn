"""Manual entitlements and immutable plan revisions. No payment or public write adapter."""

import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from vpn_api.auth.routes import Access, Service
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    PlanRevision,
    QuotaFundingLot,
    Subscription,
    SubscriptionRevocation,
    Tunnel,
)
from vpn_api.quota.contracts import MAX_BYTES, QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.schemas import ErrorResponse

GiB = 1073741824


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["subscription", "addon"]
    price_minor: int = Field(ge=0, le=MAX_BYTES)
    quota_bytes: int = Field(gt=0, le=MAX_BYTES)
    period_days: int = Field(ge=1, le=366)


class PlanView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    version: int
    name: str
    kind: str
    price_minor: int
    currency: Literal["RUB"]
    quota_bytes: int
    period_days: int
    illustrative_price: Literal[True] = True
    payments_enabled: Literal[False] = False


class SubscriptionView(BaseModel):
    id: UUID
    plan: PlanView
    basis: Literal["admin_grant"] = "admin_grant"
    starts_at: datetime
    ends_at: datetime
    state: Literal["scheduled", "active", "expired", "revoked"]
    remaining_bytes: int
    # Administrative identity/reason is deliberately excluded from user API.


class EntitlementService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def seed_demonstration_catalog(self, session: Session, *, actor: str) -> None:
        # Explicit admin setup only, never called from migrations or API startup.
        for code, name, kind, minor, quota in (
            ("start", "Start", "subscription", 9900, 100 * GiB),
            ("plus", "Plus", "subscription", 19900, 300 * GiB),
            ("extra", "10 GiB", "addon", 2900, 10 * GiB),
        ):
            self.revise_plan(
                session,
                PlanInput.model_validate(
                    {
                        "code": code,
                        "name": name,
                        "kind": kind,
                        "price_minor": minor,
                        "quota_bytes": quota,
                        "period_days": 30,
                    }
                ),
                uuid5(NAMESPACE_URL, f"detourvpn:demonstration-catalog:v1:{code}"),
                actor=actor,
                reason="Illustrative development prices; real payments disabled",
            )

    @staticmethod
    def audit(actor: str, reason: str) -> None:
        if (
            not re.fullmatch(r"[a-zA-Z0-9_.:@-]{1,128}", actor)
            or not 3 <= len(reason.strip()) <= 500
            or any(ord(char) < 32 for char in reason)
        ):
            raise QuotaError("audit_context_required", 422)

    def revise_plan(
        self, session: Session, body: PlanInput, key: UUID, *, actor: str, reason: str
    ) -> PlanRevision:
        """Trusted admin adapter must enforce MFA/RBAC before calling this method."""
        self.audit(actor, reason)
        # Low-volume catalog writes share one transaction lock, including retries
        # that reuse a key across different plan codes. No network call is made.
        session.execute(text("SELECT pg_advisory_xact_lock(21022026)"))
        prior = session.scalar(select(PlanRevision).where(PlanRevision.idempotency_key == key))
        if prior is not None:
            if any(getattr(prior, name) != value for name, value in body.model_dump().items()) or (
                prior.actor,
                prior.reason,
            ) != (actor, reason):
                raise QuotaError("plan_idempotency_conflict")
            return prior
        previous = session.scalar(
            select(PlanRevision)
            .where(PlanRevision.code == body.code)
            .order_by(PlanRevision.version.desc())
            .limit(1)
        )
        version = previous.version + 1 if previous is not None else 1
        plan = PlanRevision(
            **body.model_dump(),
            version=version,
            actor=actor,
            reason=reason,
            idempotency_key=key,
            created_at=database_now(session),
        )
        session.add(plan)
        session.flush()
        return plan

    def grant(
        self,
        session: Session,
        user_id: UUID,
        plan_id: UUID,
        key: UUID,
        *,
        actor: str,
        reason: str,
    ) -> Subscription:
        self.audit(actor, reason)
        ledger = LedgerService(self.engine)
        ledger._lock_owner(session, user_id)
        prior = session.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.idempotency_key == key
            )
        )
        if prior is not None:
            if (prior.plan_revision_id, prior.actor, prior.reason) != (plan_id, actor, reason):
                raise QuotaError("subscription_idempotency_conflict")
            return prior
        plan = session.get(PlanRevision, plan_id)
        if plan is None:
            raise QuotaError("plan_not_found", 404)
        if (
            session.scalar(
                select(Tunnel.id)
                .where(Tunnel.user_id == user_id, Tunnel.state != "revoked")
                .limit(1)
            )
            is not None
        ):
            raise QuotaError("subscription_legacy_tunnel_active")
        now = database_now(session)
        active = list(
            session.scalars(
                select(Subscription)
                .join(PlanRevision, PlanRevision.id == Subscription.plan_revision_id)
                .join(QuotaFundingLot, QuotaFundingLot.id == Subscription.funding_id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.ends_at > now,
                    PlanRevision.kind == "subscription",
                    QuotaFundingLot.revoked_at.is_(None),
                )
                .order_by(Subscription.ends_at)
            )
        )
        if plan.kind == "addon":
            current = next((item for item in active if item.starts_at <= now), None)
            if current is None:
                raise QuotaError("active_subscription_required")
            starts_at, ends_at = now, current.ends_at
        else:
            starts_at = max(now, active[-1].ends_at) if active else now
            ends_at = starts_at + timedelta(days=plan.period_days)
        subscription_id = uuid4()
        result = ledger._post(
            session,
            user_id,
            "credit",
            plan.quota_bytes,
            "internal",
            subscription_id,
            key,
            starts_at=starts_at,
            expires_at=ends_at,
        )
        if result.status != "applied":
            raise QuotaError("subscription_quota_capacity")
        subscription = Subscription(
            id=subscription_id,
            user_id=user_id,
            plan_revision_id=plan.id,
            funding_id=result.operation_id,
            idempotency_key=key,
            starts_at=starts_at,
            ends_at=ends_at,
            actor=actor,
            reason=reason,
            created_at=now,
        )
        session.add(subscription)
        session.flush()
        return subscription

    def revoke(
        self,
        session: Session,
        user_id: UUID,
        subscription_id: UUID,
        key: UUID,
        *,
        actor: str,
        reason: str,
    ) -> None:
        self.audit(actor, reason)
        LedgerService._lock_owner(session, user_id, active=False)
        subscription = session.scalar(
            select(Subscription).where(
                Subscription.id == subscription_id, Subscription.user_id == user_id
            )
        )
        if subscription is None:
            raise QuotaError("subscription_not_found", 404)
        prior = session.get(SubscriptionRevocation, subscription_id)
        if prior is not None:
            if (prior.idempotency_key, prior.actor, prior.reason) != (key, actor, reason):
                raise QuotaError("subscription_revocation_conflict")
            return
        if (
            session.scalar(
                select(SubscriptionRevocation.subscription_id).where(
                    SubscriptionRevocation.idempotency_key == key
                )
            )
            is not None
        ):
            raise QuotaError("subscription_revocation_conflict")
        now = database_now(session)
        lot = session.get(QuotaFundingLot, subscription.funding_id)
        if lot is None:
            raise QuotaError("quota_inconsistent", 503)
        lot.revoked_at = now
        session.add(
            SubscriptionRevocation(
                subscription_id=subscription_id,
                idempotency_key=key,
                actor=actor,
                reason=reason,
                created_at=now,
            )
        )
        # No network I/O in this transaction. Existing outbox is reused.
        from vpn_api.tunnels.service import enqueue_revoke
        from vpn_api.tunnels.window_service import revoke_connections

        revoke_connections(session, user_id)

        for tunnel in session.scalars(
            select(Tunnel)
            .where(Tunnel.user_id == user_id, Tunnel.state != "revoked")
            .order_by(Tunnel.id)
            .with_for_update()
        ):
            enqueue_revoke(session, tunnel)
        session.flush()

    @staticmethod
    def view(session: Session, subscription: Subscription) -> SubscriptionView:
        now = database_now(session)
        lot = session.get(QuotaFundingLot, subscription.funding_id)
        plan = session.get(PlanRevision, subscription.plan_revision_id)
        if lot is None or plan is None:
            raise QuotaError("quota_inconsistent", 503)
        state: Literal["scheduled", "active", "expired", "revoked"] = (
            "revoked"
            if lot.revoked_at is not None
            else "scheduled"
            if now < subscription.starts_at
            else "expired"
            if now >= subscription.ends_at
            else "active"
        )
        return SubscriptionView(
            id=subscription.id,
            plan=PlanView.model_validate(plan),
            starts_at=subscription.starts_at,
            ends_at=subscription.ends_at,
            state=state,
            remaining_bytes=lot.remaining_bytes,
        )


router = APIRouter(
    prefix="/api/v1",
    tags=["entitlements"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 503)},
)


@router.get("/plans", response_model=list[PlanView])
def plans(auth: Service, access: Access) -> list[PlanView]:
    with transaction(auth.engine) as session:
        auth.authenticate(session, access, "self:read")
        rows = session.scalars(
            select(PlanRevision).order_by(PlanRevision.code, PlanRevision.version.desc())
        )
        latest: dict[str, PlanRevision] = {}
        for row in rows:
            latest.setdefault(row.code, row)
        return [PlanView.model_validate(row) for row in latest.values()]


@router.get("/subscriptions", response_model=list[SubscriptionView])
def subscriptions(auth: Service, access: Access) -> list[SubscriptionView]:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "self:read")
        rows = session.scalars(
            select(Subscription)
            .where(Subscription.user_id == principal.user_id)
            .order_by(Subscription.created_at.desc())
            .limit(100)
        )
        return [EntitlementService.view(session, row) for row in rows]

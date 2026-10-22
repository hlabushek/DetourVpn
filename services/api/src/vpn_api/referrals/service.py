import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Engine, func, select, tuple_
from sqlalchemy.orm import Session

from vpn_api.auth.service import database_now
from vpn_api.db.models import (
    QuotaOperation,
    Referral,
    ReferralAward,
    ReferralCampaign,
    ReferralCode,
    Tunnel,
    TunnelReservation,
    User,
)
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.referrals.contracts import (
    AwardResponse,
    BindingResponse,
    CampaignView,
    ClaimInput,
    CodeResponse,
    OverviewResponse,
    ReferralView,
    ResolveInput,
)


class ReferralService:
    """Caller must authenticate/lock only the acting user before entering."""

    def __init__(self, engine: Engine) -> None:
        self.ledger = LedgerService(engine)

    @staticmethod
    def code(session: Session, user_id: UUID) -> CodeResponse:
        row = session.get(ReferralCode, user_id)
        if row is None:
            row = ReferralCode(user_id=user_id, code=secrets.token_urlsafe(18))
            session.add(row)
            session.flush()
        return CodeResponse(code=row.code)

    @staticmethod
    def resolve(session: Session, user_id: UUID, body: ResolveInput) -> BindingResponse:
        inviter_id = session.scalar(
            select(ReferralCode.user_id).where(ReferralCode.code == body.code)
        )
        existing = session.scalar(select(Referral).where(Referral.invitee_id == user_id))
        if existing is not None:
            if (existing.inviter_id, existing.campaign_id) != (inviter_id, body.campaign_id):
                raise QuotaError("referral_binding_conflict")
            return BindingResponse()
        if inviter_id == user_id:
            raise QuotaError("referral_self_invitation")
        campaign = session.get(ReferralCampaign, body.campaign_id)
        now = database_now(session)
        if (
            campaign is None
            or not campaign.enabled
            or not campaign.starts_at <= now < campaign.ends_at
        ):
            raise QuotaError("referral_campaign_unavailable")
        inviter = session.get(User, inviter_id) if inviter_id is not None else None
        invitee = session.get(User, user_id)
        if inviter is None or inviter.status != "active" or inviter.verified_at is None:
            raise QuotaError("referral_code_unavailable")
        if (
            invitee is None
            or invitee.verified_at is None
            or not inviter.created_at < invitee.created_at <= now
            or now - invitee.created_at > timedelta(days=7)
            or session.scalar(select(Tunnel.id).where(Tunnel.user_id == user_id).limit(1))
            is not None
        ):
            raise QuotaError("referral_binding_ineligible")
        session.add(
            Referral(
                inviter_id=inviter.id,
                invitee_id=user_id,
                campaign_id=campaign.id,
                idempotency_key=body.idempotency_key,
                reward_bytes=campaign.reward_bytes,
                created_at=now,
            )
        )
        session.flush()
        return BindingResponse()

    @staticmethod
    def overview(session: Session, user_id: UUID, before_id: UUID | None) -> OverviewResponse:
        now = database_now(session)
        query = select(Referral).where(Referral.inviter_id == user_id)
        if before_id is not None:
            cursor = session.get(Referral, before_id)
            if cursor is None or cursor.inviter_id != user_id:
                raise QuotaError("invalid_referral_cursor", 422)
            query = query.where(
                tuple_(Referral.created_at, Referral.id) < tuple_(cursor.created_at, cursor.id)
            )
        rows = list(
            session.scalars(
                query.order_by(Referral.created_at.desc(), Referral.id.desc()).limit(101)
            )
        )
        ids = [r.id for r in rows[:100]]
        awards = set(
            session.scalars(
                select(ReferralAward.referral_id).where(ReferralAward.referral_id.in_(ids))
            )
        )
        return OverviewResponse(
            code=session.scalar(select(ReferralCode.code).where(ReferralCode.user_id == user_id)),
            bound=session.scalar(select(Referral.id).where(Referral.invitee_id == user_id))
            is not None,
            invited=session.scalar(
                select(func.count()).select_from(Referral).where(Referral.inviter_id == user_id)
            )
            or 0,
            rewarded=session.scalar(
                select(func.count())
                .select_from(ReferralAward)
                .where(ReferralAward.user_id == user_id)
            )
            or 0,
            campaigns=[
                CampaignView.model_validate(c)
                for c in session.scalars(
                    select(ReferralCampaign)
                    .where(
                        ReferralCampaign.enabled.is_(True),
                        ReferralCampaign.starts_at <= now,
                        ReferralCampaign.ends_at > now,
                    )
                    .order_by(ReferralCampaign.id)
                )
            ],
            items=[
                ReferralView(
                    id=r.id,
                    created_at=r.created_at,
                    rewarded=r.id in awards,
                    reward_bytes=r.reward_bytes,
                )
                for r in rows[:100]
            ],
            next_before_id=rows[99].id if len(rows) > 100 else None,
        )

    def claim(self, session: Session, user_id: UUID, body: ClaimInput) -> AwardResponse:
        referral = session.scalar(
            select(Referral).where(Referral.id == body.referral_id, Referral.inviter_id == user_id)
        )
        if referral is None:
            raise QuotaError("referral_unavailable", 404)
        keyed = session.scalar(
            select(ReferralAward).where(
                ReferralAward.user_id == user_id,
                ReferralAward.idempotency_key == body.idempotency_key,
            )
        )
        if keyed is not None and keyed.referral_id != referral.id:
            raise QuotaError("referral_idempotency_conflict")
        existing = session.get(ReferralAward, referral.id)
        if existing is not None:
            return AwardResponse.model_validate(existing)
        campaign = session.scalar(
            select(ReferralCampaign)
            .where(ReferralCampaign.id == referral.campaign_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = database_now(session)
        if (
            campaign is None
            or not campaign.enabled
            or not campaign.starts_at <= now < campaign.ends_at
        ):
            raise QuotaError("referral_campaign_unavailable")
        invitee = session.get(User, referral.invitee_id)
        if invitee is None or invitee.status != "active" or invitee.verified_at is None:
            raise QuotaError("referral_not_qualified")
        evidence = session.scalar(
            select(Tunnel.id)
            .join(TunnelReservation, TunnelReservation.tunnel_id == Tunnel.id)
            .join(
                QuotaOperation,
                (QuotaOperation.source_id == Tunnel.id)
                & (QuotaOperation.user_id == Tunnel.user_id)
                & (QuotaOperation.source_type == "tunnel_lease")
                & (QuotaOperation.kind == "debit")
                & (QuotaOperation.status == "applied")
                & (QuotaOperation.amount_bytes == TunnelReservation.charged_bytes),
            )
            .where(
                Tunnel.user_id == invitee.id,
                Tunnel.created_at >= referral.created_at,
                Tunnel.state == "revoked",
                TunnelReservation.settled_at.is_not(None),
                TunnelReservation.observed_bytes > 0,
                TunnelReservation.charged_bytes > 0,
            )
            .limit(1)
        )
        if evidence is None:
            raise QuotaError("referral_not_qualified")
        spent = int(
            session.scalar(
                select(func.coalesce(func.sum(ReferralAward.reward_bytes), 0)).where(
                    ReferralAward.campaign_id == campaign.id
                )
            )
            or 0
        )
        count = (
            session.scalar(
                select(func.count())
                .select_from(ReferralAward)
                .where(ReferralAward.campaign_id == campaign.id, ReferralAward.user_id == user_id)
            )
            or 0
        )
        if count >= campaign.inviter_cap:
            raise QuotaError("referral_inviter_limit")
        if spent + referral.reward_bytes > campaign.budget_bytes:
            raise QuotaError("referral_budget_exhausted")
        result = self.ledger._post(
            session,
            user_id,
            "credit",
            referral.reward_bytes,
            "reward",
            referral.id,
            body.idempotency_key,
        )
        if result.status != "applied":
            raise QuotaError("quota_capacity_exceeded")
        award = ReferralAward(
            referral_id=referral.id,
            user_id=user_id,
            campaign_id=campaign.id,
            operation_id=result.operation_id,
            idempotency_key=body.idempotency_key,
            reward_bytes=referral.reward_bytes,
            balance_after_bytes=result.balance_after_bytes,
            created_at=now,
        )
        session.add(award)
        session.flush()
        return AwardResponse.model_validate(award)

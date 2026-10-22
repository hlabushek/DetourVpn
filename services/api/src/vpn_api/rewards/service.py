from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from vpn_api.auth.service import database_now
from vpn_api.db.models import RewardClaim, RewardRule
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.rewards.contracts import ClaimInput, ClaimResponse, RuleResponse, RulesResponse


class RewardService:
    def __init__(self, engine: Engine) -> None:
        self.ledger = LedgerService(engine)

    @staticmethod
    def _view(session: Session, user_id: UUID, rule: RewardRule, now: datetime) -> RuleResponse:
        day = now.astimezone(UTC).date()
        count, total = session.execute(
            select(func.count(), func.coalesce(func.sum(RewardClaim.reward_bytes), 0)).where(
                RewardClaim.user_id == user_id,
                RewardClaim.reward_type == rule.reward_type,
                RewardClaim.reward_day == day,
            )
        ).one()
        last = session.scalar(
            select(func.max(RewardClaim.created_at)).where(
                RewardClaim.user_id == user_id, RewardClaim.reward_type == rule.reward_type
            )
        )
        next_at = max(
            rule.starts_at,
            last + timedelta(seconds=rule.cooldown_seconds) if last else rule.starts_at,
        )
        remaining = max(0, rule.daily_cap_bytes - int(total))
        if count >= rule.daily_cap_claims or remaining < rule.reward_bytes:
            next_at = max(
                next_at, datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
            )
        return RuleResponse(
            id=rule.id,
            reward_type=rule.reward_type,
            reward_bytes=rule.reward_bytes,
            daily_cap_bytes=rule.daily_cap_bytes,
            daily_cap_claims=rule.daily_cap_claims,
            cooldown_seconds=rule.cooldown_seconds,
            claimed_today=count,
            remaining_bytes=remaining,
            eligible=rule.enabled and next_at <= now < rule.ends_at,
            next_claim_at=next_at,
            ends_at=rule.ends_at,
        )

    def rules(self, session: Session, user_id: UUID) -> RulesResponse:
        self.ledger._lock_owner(session, user_id)
        now = database_now(session).astimezone(UTC)
        rules = session.scalars(
            select(RewardRule)
            .where(RewardRule.enabled.is_(True))
            .order_by(RewardRule.reward_type)
            .with_for_update(read=True)
        )
        return RulesResponse(
            server_time=now,
            items=[self._view(session, user_id, rule, now) for rule in rules if now < rule.ends_at],
        )

    def claim(self, session: Session, user_id: UUID, body: ClaimInput) -> ClaimResponse:
        self.ledger._lock_owner(session, user_id)
        existing = session.scalar(
            select(RewardClaim).where(
                RewardClaim.user_id == user_id, RewardClaim.idempotency_key == body.idempotency_key
            )
        )
        if existing is not None:
            if existing.rule_id != body.rule_id:
                raise QuotaError("reward_idempotency_conflict")
            return ClaimResponse.model_validate(existing)
        rule = session.scalar(
            select(RewardRule).where(RewardRule.id == body.rule_id).with_for_update(read=True)
        )
        now = database_now(session).astimezone(UTC)
        if rule is None or not rule.enabled or not rule.starts_at <= now < rule.ends_at:
            raise QuotaError("reward_unavailable")
        view = self._view(session, user_id, rule, now)
        if not view.eligible:
            raise QuotaError(
                "reward_limit_reached"
                if (
                    view.claimed_today >= rule.daily_cap_claims
                    or view.remaining_bytes < rule.reward_bytes
                )
                else "reward_cooldown"
            )
        claim_id = uuid4()
        result = self.ledger._post(
            session, user_id, "credit", rule.reward_bytes, "reward", claim_id, body.idempotency_key
        )
        if result.status != "applied":
            raise QuotaError("quota_capacity_exceeded")
        receipt = RewardClaim(
            id=claim_id,
            user_id=user_id,
            rule_id=rule.id,
            reward_type=rule.reward_type,
            reward_bytes=rule.reward_bytes,
            reward_day=now.date(),
            created_at=now,
            idempotency_key=body.idempotency_key,
            operation_id=result.operation_id,
            balance_after_bytes=result.balance_after_bytes,
        )
        session.add(receipt)
        session.flush()
        return ClaimResponse.model_validate(receipt)

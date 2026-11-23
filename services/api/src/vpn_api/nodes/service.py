import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vpn_api.db.connection import transaction
from vpn_api.db.models import AgentRequestNonce, VPNNode
from vpn_api.nodes.contracts import ProvisionNodeInput, ProvisionNodeResult

# Agent requests older than 60 seconds are rejected before a nonce is stored, so a nonce
# cannot be replayed once it is older than that. Ten minutes leaves a wide safety margin.
NONCE_RETENTION = timedelta(minutes=10)
NONCE_PRUNE_BATCH = 5000


def cleanup_agent_request_nonces(
    engine: Engine,
    retention: timedelta = NONCE_RETENTION,
    batch: int = NONCE_PRUNE_BATCH,
) -> dict[str, int]:
    """Delete replay-protection rows beyond the replay window, in bounded batches."""
    if retention < timedelta(minutes=2) or batch < 1:
        raise ValueError("Unsafe nonce retention")
    deleted = 0
    while True:
        with transaction(engine) as session:
            now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(now, datetime):
                raise RuntimeError("Database clock unavailable")
            expired = (
                select(AgentRequestNonce.id)
                .where(AgentRequestNonce.created_at < now - retention)
                .limit(batch)
            )
            removed = session.execute(
                delete(AgentRequestNonce)
                .where(AgentRequestNonce.id.in_(expired))
                .returning(AgentRequestNonce.id)
            ).all()
        deleted += len(removed)
        if len(removed) < batch:
            return {AgentRequestNonce.__tablename__: deleted}


class NodeError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class AgentPrincipal:
    node_id: UUID
    secret_digest: str


class NodeRegistry:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def provision(
        self,
        command: ProvisionNodeInput,
        *,
        initial_status: Literal["active", "disabled"] = "active",
    ) -> ProvisionNodeResult:
        if initial_status not in {"active", "disabled"}:
            raise ValueError("node_status_invalid")
        with transaction(self.engine) as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "node-provision:" + command.idempotency_key.hex},
            )
            prior = session.scalar(
                select(VPNNode).where(VPNNode.provisioning_key == command.idempotency_key)
            )
            if prior is not None:
                if (prior.name, prior.region, prior.country_code, prior.city) != (
                    command.name,
                    command.region,
                    command.country_code,
                    command.city,
                ):
                    raise NodeError("node_provisioning_conflict")
                return ProvisionNodeResult(node_id=prior.id, replayed=True)
            raw_secret = secrets.token_urlsafe(32)
            node = VPNNode(
                id=uuid4(),
                provisioning_key=command.idempotency_key,
                name=command.name,
                region=command.region,
                country_code=command.country_code,
                city=command.city,
                status=initial_status,
                secret_digest=hashlib.sha256(raw_secret.encode("ascii")).hexdigest(),
            )
            session.add(node)
            try:
                session.flush()
            except IntegrityError:
                raise NodeError("node_provisioning_conflict") from None
            return ProvisionNodeResult(node_id=node.id, secret=raw_secret)

    @staticmethod
    def verify_and_consume(
        session: Session,
        *,
        node_id: UUID,
        timestamp: str,
        nonce: UUID,
        credential: str,
        now: datetime | None = None,
    ) -> AgentPrincipal:
        current = now or datetime.now(UTC)
        if (
            len(timestamp) != 10
            or not timestamp.isascii()
            or not timestamp.isdecimal()
            or len(credential) != 43
            or not credential.isascii()
        ):
            raise NodeError("agent_unauthorized", 401)
        try:
            issued = datetime.fromtimestamp(int(timestamp), UTC)
        except (ValueError, OverflowError, OSError):
            raise NodeError("agent_unauthorized", 401) from None
        if abs(current - issued) > timedelta(seconds=60):
            raise NodeError("agent_unauthorized", 401)
        node = session.scalar(select(VPNNode).where(VPNNode.id == node_id).with_for_update())
        if node is None or node.status != "active":
            raise NodeError("agent_unauthorized", 401)
        supplied = hashlib.sha256(credential.encode("ascii", errors="ignore")).hexdigest()
        if not secrets.compare_digest(node.secret_digest, supplied):
            raise NodeError("agent_unauthorized", 401)
        session.add(
            AgentRequestNonce(
                id=uuid4(),
                node_id=node.id,
                secret_digest=node.secret_digest,
                nonce=nonce,
                created_at=current,
            )
        )
        try:
            session.flush()
        except IntegrityError:
            raise NodeError("agent_replay", 409) from None
        # ADR-178: last_seen_at uses the database clock, the same clock admission compares
        # against. `current` (the API host's clock) only checks the agent's request timestamp.
        node.last_seen_at = session.scalar(select(func.clock_timestamp()))
        return AgentPrincipal(node.id, node.secret_digest)

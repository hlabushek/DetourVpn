import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vpn_api.db.connection import transaction
from vpn_api.db.models import AgentRequestNonce, VPNNode
from vpn_api.nodes.contracts import ProvisionNodeInput, ProvisionNodeResult


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

    def provision(self, command: ProvisionNodeInput) -> ProvisionNodeResult:
        with transaction(self.engine) as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "node-provision:" + command.idempotency_key.hex},
            )
            prior = session.scalar(
                select(VPNNode).where(VPNNode.provisioning_key == command.idempotency_key)
            )
            if prior is not None:
                if (prior.name, prior.region) != (command.name, command.region):
                    raise NodeError("node_provisioning_conflict")
                return ProvisionNodeResult(node_id=prior.id, replayed=True)
            raw_secret = secrets.token_urlsafe(32)
            node = VPNNode(
                id=uuid4(),
                provisioning_key=command.idempotency_key,
                name=command.name,
                region=command.region,
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
        node.last_seen_at = current
        return AgentPrincipal(node.id, node.secret_digest)

"""TEST ONLY (M7, M8, ADR-178): one clock source for last_seen_at; node clocks checked at install.

The PostgreSQL test needs the temporary cluster (scripts/test_with_postgres.py). The candidate
checks run on fixed facts; nothing contacts a host.
"""

import subprocess  # noqa: S404 - the test replaces subprocess.run; no command is executed
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from scripts import check_candidate_node as check
from sqlalchemy import Engine, func, select
from test_migrations import migration_config

from vpn_api.db.connection import transaction
from vpn_api.db.models import VPNNode
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeRegistry

pytestmark = pytest.mark.postgres

GOOD = {
    "os_id": "ubuntu",
    "os_version": "24.04",
    "arch": "x86_64",
    "uid": "0",
    "cpus": "2",
    "mem_available_kb": "1500000",
    "root_free_kb": "20000000",
    "ip_forward": "1",
    "default_routes": "1",
    "host_ip_assigned": "1",
    "systemd": "1",
    "systemd_executor": "1",
    "udp_ports_busy": "0",
    "ntp_synchronized": "yes",
    "clock_offset_ms": "0",
}


def verdict(values: dict[str, str]) -> dict[str, bool]:
    return {str(c["check"]): bool(c["ok"]) for c in check.evaluate(values)}


@pytest.mark.parametrize(
    ("changes", "failing"),
    [
        ({"ntp_synchronized": "no"}, "ntp_synchronized"),
        ({"ntp_synchronized": "unknown"}, "ntp_synchronized"),
        ({"clock_offset_ms": "1001"}, "clock_within_1s"),
        ({"clock_offset_ms": "-1001"}, "clock_within_1s"),
        ({"clock_offset_ms": "unknown"}, "clock_within_1s"),
        ({"clock_offset_ms": "-1000"}, None),
        ({"clock_offset_ms": "1000"}, None),
    ],
)
def test_install_requires_ntp_and_a_clock_within_one_second(
    changes: dict[str, str], failing: str | None
) -> None:
    result = verdict({**GOOD, **changes})
    if failing is None:
        assert all(result.values())
    else:
        assert result[failing] is False
        required = {c["check"]: c["required"] for c in check.evaluate({**GOOD, **changes})}
        assert required[failing] is True


def test_remote_offset_is_measured_around_the_ssh_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class Node:
        host, ssh_port, ssh_user, ssh_key = "192.0.2.5", 22, "root", "k"
        udp_ports = (51901, 51916)

    clock = iter([1_000.0, 1_000.4])  # local round trip 0.4 s, midpoint 1000.2 s
    monkeypatch.setattr(check, "time", SimpleNamespace(time=lambda: next(clock)))
    remote = "clock_epoch_ms=1000300\n"
    monkeypatch.setattr(
        check,
        "subprocess",
        SimpleNamespace(
            run=lambda *_a, **_k: subprocess.CompletedProcess(
                [], 0, stdout=remote.encode(), stderr=b""
            ),
        ),
    )
    values = check.run_remote(Node())  # type: ignore[arg-type]
    assert values["clock_offset_ms"] == "100"  # 1000300 - 1000200


@pytest.mark.postgres
def test_last_seen_comes_from_the_database_clock_not_the_api_host(engine: Engine) -> None:
    """M8 (ADR-178): admission compares last_seen_at with the database clock; it must match it."""
    command.upgrade(migration_config(engine), "head")
    registry = NodeRegistry(engine)
    provisioned = registry.provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="node", region="eu-test")
    )
    assert provisioned.secret is not None
    host_now = datetime.now(UTC) + timedelta(seconds=30)  # an API host clock 30 s ahead
    timestamp = str(int(datetime.now(UTC).timestamp()))
    with transaction(engine) as session:
        registry.verify_and_consume(
            session,
            node_id=provisioned.node_id,
            timestamp=timestamp,
            nonce=uuid4(),
            credential=provisioned.secret,
            now=host_now,
        )
    with transaction(engine) as session:
        last_seen = session.get(VPNNode, provisioned.node_id).last_seen_at  # type: ignore[union-attr]
        database_now = session.scalar(select(func.clock_timestamp()))
    assert last_seen is not None and database_now is not None
    assert abs((database_now - last_seen).total_seconds()) < 5
    assert last_seen < host_now - timedelta(seconds=20)

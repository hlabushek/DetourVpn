"""TEST ONLY audit stage 2 (C1, ADR-171): rate-limit identity behind the reverse proxy.

Before ADR-171 every client reaching the API through Caddy shared one per-network bucket,
and unauthenticated callers could use up the bucket of authenticated users. No external
service is contacted; addresses are documentation ranges.
"""

import re
from collections.abc import Iterator
from ipaddress import IPv4Address
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from test_authentication import Harness, harness, headers, login

from vpn_api.auth.routes import FORWARDED_INVALID, network
from vpn_api.main import create_app

__all__ = ["harness"]
ROOT = Path(__file__).resolve().parents[3]
PROXY = IPv4Address("127.0.0.1")


def proxied(
    forwarded_for: list[str], *, peer: str = "127.0.0.1", trusted: IPv4Address | None = PROXY
) -> Request:
    # What uvicorn builds behind Caddy with --no-proxy-headers: the peer is always the proxy.
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"x-forwarded-for", value.encode("ascii")) for value in forwarded_for],
            "client": (peer, 40000),
            "app": SimpleNamespace(state=SimpleNamespace(trusted_proxy=trusted)),
        }
    )


def test_deployment_puts_every_client_behind_one_loopback_peer() -> None:
    unit = (ROOT / "infra/beta/detourvpn-beta-api.service").read_text(encoding="utf-8")
    caddy = (ROOT / "infra/beta/Caddyfile.ip").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in unit and "--no-proxy-headers" in unit
    assert re.search(r"reverse_proxy 127\.0\.0\.1:28000", caddy)


def test_caddy_overwrites_the_forwarded_header_on_every_proxied_route() -> None:
    for path in ("infra/beta/Caddyfile.ip", "scripts/deploy_ha_server.sh"):
        text = (ROOT / path).read_text(encoding="utf-8")
        proxies = re.findall(r"reverse_proxy [^\n]*\{\n\s*header_up ([^\n]+)\n", text)
        assert proxies and proxies.count("X-Forwarded-For {remote_host}") == text.count(
            "reverse_proxy "
        ), path


def test_distinct_clients_behind_the_proxy_get_distinct_rate_keys() -> None:
    assert network(proxied(["203.0.113.10"])) != network(proxied(["198.51.100.20"]))
    assert network(proxied(["203.0.113.10"])) == "203.0.113.10"


def test_forwarded_header_is_ignored_unless_the_peer_is_the_trusted_proxy() -> None:
    # Not configured: the previous behaviour (direct peer only).
    assert network(proxied(["203.0.113.10"], trusted=None)) == "127.0.0.1"
    # A direct client cannot choose its own bucket with a forged header.
    assert network(proxied(["203.0.113.10"], peer="198.51.100.7")) == "198.51.100.7"


@pytest.mark.parametrize(
    "values",
    [[], ["203.0.113.10, 198.51.100.20"], ["203.0.113.10", "198.51.100.20"], ["not-an-ip"]],
    ids=["missing", "chain", "repeated", "garbage"],
)
def test_malformed_forwarded_header_from_the_proxy_fails_closed(values: list[str]) -> None:
    assert network(proxied(values)) == FORWARDED_INVALID


def test_ipv6_clients_are_grouped_by_64() -> None:
    first = network(proxied(["2001:db8:1:2::10"]))
    assert first == "2001:db8:1:2::/64" == network(proxied(["2001:db8:1:2:ffff::1"]))


@pytest.fixture
def behind_proxy(harness: Harness) -> Iterator[TestClient]:
    """The API as deployed: every request arrives from the trusted local proxy."""
    settings = harness.settings.model_copy(update={"trusted_proxy_address": PROXY})
    app = create_app(settings, database_engine=harness.engine, telegram_sender=harness.sender)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


def native_renew(client: TestClient, forwarded_for: str) -> int:
    body = {
        "previous_window_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "budget_bytes": 65536,
    }
    return client.post(
        f"/api/v1/window-tunnels/{uuid4()}/native-renew",
        json=body,
        headers={"X-Forwarded-For": forwarded_for},
    ).status_code


@pytest.mark.postgres
def test_unauthenticated_requests_do_not_exhaust_another_clients_bucket(
    behind_proxy: TestClient,
) -> None:
    statuses = [native_renew(behind_proxy, "203.0.113.10") for _ in range(121)]
    assert statuses[:120] == [401] * 120 and statuses[120] == 429  # the attacker's own bucket
    assert native_renew(behind_proxy, "198.51.100.20") == 401, "another client stays unaffected"


@pytest.mark.postgres
def test_authenticated_window_limit_is_per_user_device(
    harness: Harness, behind_proxy: TestClient
) -> None:
    first, second = login(harness, 8101), login(harness, 8102)
    path = f"/api/v1/window-tunnels/{uuid4()}"
    same_network = {"X-Forwarded-For": "203.0.113.30"}
    statuses = [
        behind_proxy.delete(path, headers={**headers(first), **same_network}).status_code
        for _ in range(31)
    ]
    assert statuses[:30] == [404] * 30 and statuses[30] == 429
    # Another user on the same network (for example a carrier NAT) keeps working.
    response = behind_proxy.delete(path, headers={**headers(second), **same_network})
    assert response.status_code == 404

"""TEST ONLY audit stage 2 (H5, ADR-172): host FORWARD policy outside the bootstrap's tables.

Fake command runner only; never touches host networking. An nftables accept in one base
chain does not stop a later base chain on the same hook (for example ufw's iptables-nft
FORWARD chain with policy drop) from dropping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "node-agent"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

import beta_slot_bootstrap as bootstrap  # noqa: E402
from test_beta_slot_bootstrap import slot_source  # noqa: E402

UFW_RULESET = {
    "nftables": [
        {"table": {"family": "ip", "name": "filter"}},
        {
            "chain": {
                "family": "ip",
                "table": "filter",
                "name": "FORWARD",
                "type": "filter",
                "hook": "forward",
                "prio": 0,
                "policy": "drop",
            }
        },
        {"chain": {"family": "ip", "table": "filter", "name": "ufw-before-forward"}},
    ]
}


def route_rule(inbound: str, outbound: str) -> dict[str, object]:
    """What iptables-nft lists for `ufw route allow in on <in> out on <out>`."""
    return {
        "rule": {
            "family": "ip",
            "table": "filter",
            "chain": "ufw-user-forward",
            "expr": [
                {"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": inbound}},
                {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": outbound}},
                {"counter": {"packets": 0, "bytes": 0}},
                {"accept": None},
            ],
        }
    }


RULESET: dict[str, list[object]] = {"nftables": list(UFW_RULESET["nftables"])}


def fake_host(arguments: list[str], *, data: str | None = None, required: bool = True) -> str:
    del data, required
    joined = " ".join(arguments[1:])
    if joined == "-j -4 route get 1.1.1.1":
        return json.dumps([{"dev": "eth0", "prefsrc": "8.8.8.8"}])
    if joined == "-j -4 address show dev eth0":
        return json.dumps([{"addr_info": [{"local": "8.8.8.8", "prefixlen": 24}]}])
    if joined == "-j -4 route show table all":
        return json.dumps([{"dst": "default"}])
    if joined == "-j -4 address show":
        return json.dumps([{"addr_info": [{"local": "8.8.8.8", "prefixlen": 24}]}])
    if joined in {"-j list tables", "-j list ruleset"}:
        return json.dumps(RULESET)
    return ""  # ss: no port in use; ip link show: no veth


def test_preflight_refuses_a_host_whose_forward_policy_drops_routed_traffic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = bootstrap.Slots.model_validate_json(slot_source()).slots
    monkeypatch.setattr(bootstrap.Path, "read_text", lambda _path: "1")
    monkeypatch.setattr(bootstrap, "_run", fake_host)
    with pytest.raises(bootstrap.BootstrapError, match="host_forward_policy_drops"):
        bootstrap._host_network_preflight(slots)


@pytest.mark.parametrize(
    ("rules", "accepted"),
    [
        ([route_rule("dvbh1", "eth0"), route_rule("eth0", "dvbh1")], True),
        ([route_rule("dvbh1", "eth0")], False),  # return direction missing
        ([route_rule("dvbh2", "eth0"), route_rule("eth0", "dvbh2")], False),  # other slot
    ],
    ids=["both-directions", "one-direction", "wrong-veth"],
)
def test_preflight_accepts_drop_policy_only_with_route_rules_for_every_slot(
    monkeypatch: pytest.MonkeyPatch, rules: list[dict[str, object]], accepted: bool
) -> None:
    slots = bootstrap.Slots.model_validate_json(slot_source()).slots
    ruleset = {"nftables": [*UFW_RULESET["nftables"], *rules]}
    monkeypatch.setattr(sys.modules[__name__], "RULESET", ruleset)
    monkeypatch.setattr(bootstrap.Path, "read_text", lambda _path: "1")
    monkeypatch.setattr(bootstrap, "_run", fake_host)
    if accepted:
        assert bootstrap._host_network_preflight(slots) == "eth0"
    else:
        with pytest.raises(bootstrap.BootstrapError, match="host_forward_policy_drops"):
            bootstrap._host_network_preflight(slots)


def test_preflight_accepts_a_host_without_a_dropping_forward_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = bootstrap.Slots.model_validate_json(slot_source()).slots
    monkeypatch.setattr(sys.modules[__name__], "RULESET", {"nftables": []})
    monkeypatch.setattr(bootstrap.Path, "read_text", lambda _path: "1")
    monkeypatch.setattr(bootstrap, "_run", fake_host)
    assert bootstrap._host_network_preflight(slots) == "eth0"


def test_install_failure_names_the_manual_route_rules_with_placeholders() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.install_beta_node import UFW_ROUTE_RULES, forward_policy_hint

    hint = forward_policy_hint("install_failed:bootstrap:error_host_forward_policy_drops")
    assert hint == list(UFW_ROUTE_RULES)
    assert all("<UPLINK>" in line and "dvbh<N>" in line for line in hint)
    assert forward_policy_hint("install_failed:bootstrap:error_beta_awg_port_in_use") is None
    runbook = (ROOT / "docs/runbooks/beta-add-node.md").read_text(encoding="utf-8")
    assert all(line in runbook for line in hint)

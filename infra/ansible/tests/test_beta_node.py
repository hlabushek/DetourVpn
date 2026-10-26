"""Static contract tests for the unprovisioned beta node daemon unit."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UNIT = ROOT / "infra" / "beta-node" / "detourvpn-beta-node-agent.service"


def directives(unit: str, section: str) -> dict[str, str]:
    current = ""
    result: dict[str, str] = {}
    for raw in unit.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def test_agent_unit_uses_only_fixed_beta_node_paths() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    service = directives(unit, "Service")
    conditions = [
        line.strip() for line in unit.splitlines() if line.strip().startswith("ExecCondition=")
    ]
    assert (
        service["ExecStart"]
        == "/opt/detourvpn-node/.venv/bin/python /opt/detourvpn-node/window_daemon.py"
    )
    assert (
        service["ExecStartPre"]
        == "/opt/detourvpn-node/.venv/bin/python /opt/detourvpn-node/beta_node_preflight.py"
    )
    assert service["WorkingDirectory"] == "/opt/detourvpn-node"
    assert service["User"] == service["Group"] == "root"
    assert "/etc/detourvpn-node/credential" not in service["ExecStart"]
    assert conditions == [
        "ExecCondition=/usr/bin/test -f /etc/detourvpn-node/agent.json",
        "ExecCondition=/usr/bin/test -f /etc/detourvpn-node/credential",
        "ExecCondition=/usr/bin/test -f /etc/detourvpn-node/slots.json",
    ]
    assert not any(key in {"Environment", "EnvironmentFile"} for key in service)
    assert "/var/lib/detourvpn-node" in unit


def test_agent_unit_has_fail_closed_startup_and_supervision() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    service = directives(unit, "Service")
    assert "/etc/detourvpn-node/slots.json" in unit
    assert service["Restart"] == "on-failure"
    assert service["RestartSec"] == "3"
    assert service["TimeoutStopSec"] == "15"


def test_agent_unit_limits_writes_and_network_families() -> None:
    service = directives(UNIT.read_text(encoding="utf-8"), "Service")
    assert service["UMask"] == "0077"
    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["ProtectHome"] == "true"
    assert service["PrivateTmp"] == "true"
    assert service["StateDirectory"] == "detourvpn-node"
    assert service["StateDirectoryMode"] == "0700"
    assert service["ReadWritePaths"].split() == ["/run/detourvpn-node", "/var/lib/detourvpn-node"]
    assert service["RestrictAddressFamilies"].split() == [
        "AF_INET",
        "AF_INET6",
        "AF_UNIX",
        "AF_NETLINK",
    ]

"""Synthetic dump strings only; no Android/VPN evidence."""

import json

from test_i019_live import normal_android_network


def test_default_network_requires_validated_physical_transport_and_no_vpn() -> None:
    raw = (
        "Active default network: 123\nNetworkAgentInfo{network{123} Transports: CELLULAR "
        "Capabilities: INTERNET&NOT_VPN&VALIDATED SSID: TEST-PRIVATE}"
    )
    state = normal_android_network(raw)
    assert state == {
        "identified": True,
        "vpn": False,
        "validated": True,
        "not_vpn": True,
        "normal": True,
    }
    assert "TEST-PRIVATE" not in json.dumps(state)
    vpn = raw.replace("CELLULAR", "VPN").replace("&NOT_VPN", "")
    assert not normal_android_network(vpn)["normal"]
    assert not normal_android_network(raw.replace("&VALIDATED", ""))["normal"]


def test_unknown_or_ambiguous_default_network_fails_closed() -> None:
    assert not normal_android_network("TEST-UNKNOWN")["normal"]
    row = "NetworkAgentInfo{network{123} Transports: WIFI Capabilities: NOT_VPN&VALIDATED}"
    assert not normal_android_network("Active default network: 123\n" + row + "\n" + row)["normal"]
    assert not normal_android_network("Active default network: 321\n" + row)["normal"]

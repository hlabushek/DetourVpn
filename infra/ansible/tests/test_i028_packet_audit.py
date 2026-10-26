"""TEST ONLY parser/filter tests; packet headers below are synthetic fixtures."""

import ipaddress
import os
from collections.abc import Sequence

import pytest
from scripts import i028_packet_audit as audit

CLIENT4 = ipaddress.ip_address("192.0.2.10")
CLIENT6 = ipaddress.ip_address("2001:db8::2")
ENDPOINT = ipaddress.ip_address("198.51.100.20")


def test_synthetic_headers_count_dns_ipv6_and_tunnel_without_emitting_addresses() -> None:
    lines = [
        "1.0 IP 192.0.2.10.54321 > 1.1.1.1.53: UDP, length 40",
        "2.0 IP6 2001:db8::2.50000 > 2606:4700:4700::1111.443: UDP, length 88",
        "3.0 IP6 2001:db8::2.53 > 2606:4700:4700::1111.53: UDP, length 60",
        "4.0 IP6 2001:db8::2.50001 > ff02::1.53: UDP, length 60",
        "5.0 IP 192.0.2.10.51820 > 198.51.100.20.51820: UDP, length 1200",
        "6.0 IP 192.0.2.99.51000 > 9.9.9.9.53: UDP, length 44",
    ]

    report = audit.summarize_headers(
        lines, clients={CLIENT4, CLIENT6}, vpn_endpoint=ENDPOINT, phase="included"
    )

    assert report == {
        "test_only": True,
        "phase": "included",
        "packet_count": 5,
        "direct_dns_v4_packets": 1,
        "direct_dns_v6_packets": 1,
        "global_ipv6_egress_packets": 2,
        "vpn_outer_endpoint_packets": 1,
    }
    assert set(report) == {
        "test_only",
        "phase",
        "packet_count",
        "direct_dns_v4_packets",
        "direct_dns_v6_packets",
        "global_ipv6_egress_packets",
        "vpn_outer_endpoint_packets",
    }
    assert "192.0.2.10" not in repr(report)
    assert "2606:4700" not in repr(report)


@pytest.mark.parametrize(
    "line",
    [
        "IP phone.example.1234 > 1.1.1.1.53: UDP, length 10",
        "1.0 IP 192.0.2.10 > 1.1.1.1: UDP, length 10",
        "1.0 IP6 192.0.2.10.123 > 2001:db8::1.53: UDP, length 10",
        "1.0 IP6 2001:db8::2.123 > bad-name.53: UDP, length 10",
        "1.0 IP6 2001:db8::2 > 2606:4700::1: truncated-ip6 - 300 bytes missing!",
    ],
)
def test_synthetic_malformed_or_name_resolving_lines_fail_closed(line: str) -> None:
    with pytest.raises(audit.AuditError, match="capture_parse_failed"):
        audit.parse_packet_line(line)


def test_capture_filter_requires_explicit_client_addresses_and_excludes_link_local() -> None:
    value = audit.build_filter({CLIENT4, CLIENT6}, ENDPOINT)
    assert "src host 192.0.2.10" in value
    assert "src host 2001:db8::2" in value
    assert "udp port 53" in value and "tcp port 53" in value
    assert "dst net 2000::/3" in value
    assert "dst host 198.51.100.20" in value
    assert "not dst net fe80::/10" in value


class _TestOnlyProcess:
    def __init__(self, command: Sequence[str], **kwargs: object) -> None:
        self.command = list(command)
        self.kwargs = kwargs
        self.returncode = 0
        self.stdout = object()

    def communicate(self, timeout: int) -> tuple[bytes, None]:
        assert timeout == 10
        return b"1.0 IP 192.0.2.10.123 > 1.1.1.1.53: UDP, length 40\n", None

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: int) -> int:
        return self.returncode


def test_capture_uses_bounded_no_shell_no_pcap_path_and_returns_aggregates(monkeypatch) -> None:
    spawned: list[_TestOnlyProcess] = []
    filters: list[str] = []

    def fake_popen(command: Sequence[str], **kwargs: object) -> _TestOnlyProcess:
        filter_fd = kwargs["pass_fds"][0]  # type: ignore[index]
        with os.fdopen(os.dup(filter_fd), "r", encoding="ascii") as filter_stream:
            filters.append(filter_stream.read())
        process = _TestOnlyProcess(command, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(audit.shutil, "which", lambda _: "/usr/sbin/tcpdump")
    monkeypatch.setattr(audit.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(audit, "_proc_fd_available", lambda: True)
    report = audit.capture(
        interface="wlan0",
        clients={CLIENT4},
        endpoint=ENDPOINT,
        phase="baseline",
        duration=10,
        max_packets=100,
    )

    assert report["direct_dns_v4_packets"] == 1
    assert report["phase"] == "baseline"
    assert report["bounded_window_ended"] is False
    assert len(spawned) == 1
    assert spawned[0].kwargs["shell"] is False
    assert "-w" not in spawned[0].command
    assert "-c" in spawned[0].command
    assert spawned[0].command[spawned[0].command.index("-s") + 1] == "256"
    assert "-F" in spawned[0].command
    assert "src host 192.0.2.10" in filters[0]
    assert "dst host 198.51.100.20" in filters[0]
    assert "192.0.2.10" not in repr(spawned[0].command)
    assert "198.51.100.20" not in repr(spawned[0].command)


def test_capture_rejects_unbounded_or_shell_like_inputs() -> None:
    with pytest.raises(audit.AuditError, match="interface_invalid"):
        audit.capture(
            interface="wlan0;rm",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            phase="included",
            duration=10,
            max_packets=100,
        )
    with pytest.raises(audit.AuditError, match="duration_invalid"):
        audit.capture(
            interface="wlan0",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            phase="included",
            duration=181,
            max_packets=100,
        )
    with pytest.raises(audit.AuditError, match="packet_limit_invalid"):
        audit.capture(
            interface="wlan0",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            phase="included",
            duration=10,
            max_packets=5001,
        )

"""TEST ONLY parser/filter tests; packet headers below are synthetic fixtures."""

import ipaddress
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime
from types import SimpleNamespace

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
        "5.0 IP 192.0.2.10.51820 > 198.51.100.20.51901: UDP, length 1200",
        "5.1 IP 192.0.2.10.51820 > 198.51.100.20.51902: UDP, length 1200",
        "6.0 IP 192.0.2.99.51000 > 9.9.9.9.53: UDP, length 44",
        "7.0 IP 192.0.2.10.54322 > 1.1.1.1.53: Flags [S], seq 1, win 1024, length 0",
        (
            "8.0 IP6 2001:db8::2.50002 > 2606:4700:4700::1111.443: "
            "Flags [S], seq 1, win 1024, length 0"
        ),
        "9.0 IP6 2001:db8::2 > 2606:4700::1: ICMP6, echo request, length 16",
    ]

    report = audit.summarize_headers(
        lines,
        clients={CLIENT4, CLIENT6},
        vpn_endpoint=ENDPOINT,
        vpn_endpoint_ports=(51901, 51902),
        phase="included",
    )

    expected_keys = {
        "test_only",
        "phase",
        "packet_count",
        "direct_dns_v4_packets",
        "direct_dns_v6_packets",
        "global_ipv6_egress_packets",
        "vpn_outer_endpoint_packets",
        "vpn_outer_endpoint_ports",
        "phase_scope",
        "direction",
        "client_filter_mode",
        "direct_dns_v4_udp_egress_packets",
        "direct_dns_v4_tcp_egress_packets",
        "direct_dns_v6_udp_egress_packets",
        "direct_dns_v6_tcp_egress_packets",
        "probe_dns_source_port_packets",
        "probe_ipv6_https_source_port_packets",
        "probe_baseline_ipv6_https_source_port_packets",
        "global_ipv6_egress_udp_packets",
        "global_ipv6_egress_tcp_packets",
        "global_ipv6_egress_icmp_packets",
        "global_ipv6_egress_other_packets",
        "global_ipv6_egress_port_53_packets",
        "global_ipv6_egress_port_443_packets",
        "global_ipv6_egress_other_port_packets",
        "global_ipv6_egress_no_transport_port_packets",
        "vpn_outer_endpoint_udp_egress_packets",
        "vpn_outer_endpoint_tcp_egress_packets",
        "vpn_outer_endpoint_other_egress_packets",
    }
    expected_keys.update(
        f"global_ipv6_egress_{protocol}_{port_category}_packets"
        for protocol in audit.GLOBAL_V6_PROTOCOLS
        for port_category in audit.GLOBAL_V6_PORT_CATEGORIES
    )
    assert set(report) == expected_keys
    assert report["packet_count"] == 9
    assert report["direction"] == "egress"
    assert report["client_filter_mode"] == "explicit_addresses"
    assert report["direct_dns_v4_udp_egress_packets"] == 1
    assert report["direct_dns_v4_tcp_egress_packets"] == 1
    assert report["direct_dns_v6_udp_egress_packets"] == 1
    assert report["direct_dns_v6_tcp_egress_packets"] == 0
    assert report["global_ipv6_egress_packets"] == 4
    assert report["global_ipv6_egress_udp_packets"] == 2
    assert report["global_ipv6_egress_tcp_packets"] == 1
    assert report["global_ipv6_egress_icmp_packets"] == 1
    assert report["global_ipv6_egress_port_53_packets"] == 1
    assert report["global_ipv6_egress_port_443_packets"] == 2
    assert report["global_ipv6_egress_udp_port_443_packets"] == 1
    assert report["global_ipv6_egress_udp_port_53_packets"] == 1
    assert report["global_ipv6_egress_tcp_port_443_packets"] == 1
    assert report["global_ipv6_egress_icmp_no_transport_port_packets"] == 1
    assert report["vpn_outer_endpoint_packets"] == 2
    assert report["vpn_outer_endpoint_ports"] == [51901, 51902]
    assert report["phase_scope"] == "operator_labeled_window"
    assert report["vpn_outer_endpoint_udp_egress_packets"] == 2
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


def test_node_agent_port_is_not_counted_as_i028_tunnel_endpoint() -> None:
    report = audit.summarize_headers(
        ["1.0 IP 192.0.2.10.50000 > 198.51.100.20.51892: UDP, length 1200"],
        clients={CLIENT4},
        vpn_endpoint=ENDPOINT,
        vpn_endpoint_ports=audit.DEFAULT_VPN_ENDPOINT_PORTS,
        phase="included",
    )
    assert report["vpn_outer_endpoint_packets"] == 0
    assert report["vpn_outer_endpoint_ports"] == [51901, 51902]


def test_vpn_trigger_matches_only_fixed_outgoing_udp_endpoint() -> None:
    tunnel = audit.parse_packet_line(
        "1.0 IP 192.0.2.10.50000 > 198.51.100.20.51901: UDP, length 1200"
    )
    node_agent = audit.parse_packet_line(
        "2.0 IP 192.0.2.10.50000 > 198.51.100.20.51892: UDP, length 1200"
    )
    tcp = audit.parse_packet_line(
        "3.0 IP 192.0.2.10.50000 > 198.51.100.20.51901: Flags [S], length 0"
    )

    assert audit.is_vpn_outer_packet(tunnel, ENDPOINT, (51901, 51902))
    assert not audit.is_vpn_outer_packet(node_agent, ENDPOINT, (51901, 51902))
    assert not audit.is_vpn_outer_packet(tcp, ENDPOINT, (51901, 51902))


def test_excluded_probe_trigger_matches_only_fixed_direct_probe_flows() -> None:
    dns = audit.parse_packet_line(
        "1.0 IP 192.0.2.10.42428 > 1.1.1.1.53: UDP, length 40"
    )
    ipv6 = audit.parse_packet_line(
        "2.0 IP6 2001:db8::2.42430 > 2606:4700:4700::1111.443: Flags [S], length 0"
    )
    unrelated = audit.parse_packet_line(
        "3.0 IP6 2001:db8::2.42429 > 2606:4700:4700::1111.443: Flags [S], length 0"
    )
    assert audit.is_excluded_probe_packet(dns)
    assert audit.is_excluded_probe_packet(ipv6)
    assert not audit.is_excluded_probe_packet(unrelated)


def test_triggered_capture_discards_pre_vpn_headers(monkeypatch) -> None:
    class ReadySelector:
        def register(self, *_args: object) -> None:
            pass

        def select(self, _timeout: float) -> list[tuple[None, None]]:
            return [(None, None)]

        def close(self) -> None:
            pass

    monkeypatch.setattr(audit.selectors, "DefaultSelector", ReadySelector)
    data = (
        "0.0 IP6 2001:db8::2.50000 > 2606:4700:4700::1111.443: UDP, length 88\n"
        "1.0 IP 192.0.2.10.50000 > 198.51.100.20.51901: UDP, length 1200\n"
        "1.1 IP 192.0.2.10.54321 > 1.1.1.1.53: UDP, length 40\n"
        "1.2 IP6 2001:db8::2.50001 > 2606:4700:4700::1111.443: UDP, length 88\n"
    )
    with tempfile.TemporaryFile(mode="w+b") as stream:
        stream.write(data.encode("ascii"))
        stream.seek(0)
        lines, wait_seconds, trigger_at_utc, window_ended, packet_limit_ended = (
            audit.collect_headers_after_vpn_trigger(
                stream,
                vpn_endpoint=ENDPOINT,
                vpn_endpoint_ports=(51901, 51902),
                duration=30,
                start_timeout=10,
            )
        )

    report = audit.summarize_headers(
        lines,
        clients={CLIENT4, CLIENT6},
        vpn_endpoint=ENDPOINT,
        vpn_endpoint_ports=(51901, 51902),
        phase="included",
    )
    assert wait_seconds >= 0
    assert datetime.fromisoformat(trigger_at_utc).utcoffset() is not None
    assert window_ended is False
    assert packet_limit_ended is True
    assert report["vpn_outer_endpoint_packets"] == 1
    assert report["direct_dns_v4_packets"] == 1
    assert report["global_ipv6_egress_packets"] == 1


def test_triggered_capture_fails_closed_when_endpoint_is_not_seen(monkeypatch) -> None:
    class EmptySelector:
        def register(self, *_args: object) -> None:
            pass

        def select(self, _timeout: float) -> list[tuple[None, None]]:
            return []

        def close(self) -> None:
            pass

    monkeypatch.setattr(audit.selectors, "DefaultSelector", EmptySelector)
    with tempfile.TemporaryFile(mode="w+b") as stream:
        with pytest.raises(audit.AuditError, match="capture_trigger_not_seen"):
            audit.collect_headers_after_vpn_trigger(
                stream,
                vpn_endpoint=ENDPOINT,
                vpn_endpoint_ports=(51901, 51902),
                duration=30,
                start_timeout=10,
            )


def test_excluded_trigger_discards_prior_packets_and_reports_only_post_trigger_counts(
    monkeypatch,
) -> None:
    class ReadyOnceSelector:
        def __init__(self) -> None:
            self.calls = 0

        def register(self, *_args: object) -> None:
            pass

        def select(self, _timeout: float) -> list[tuple[None, None]]:
            self.calls += 1
            return [(None, None)] if self.calls == 1 else []

        def close(self) -> None:
            pass

    class PipeProcess:
        def __init__(self, stream, write_fd: int) -> None:
            self.returncode = None
            self.stdout = stream
            self.write_fd = write_fd

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9
            os.close(self.write_fd)

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            self.returncode = 0
            return 0

    output = (
        b"0.0 IP6 2001:db8::2.50000 > 2606:4700:4700::1111.443: UDP, length 88\n"
        b"1.0 IP 192.0.2.10.54321 > 1.1.1.1.53: UDP, length 40\n"
        b"2.0 IP 192.0.2.10.42428 > 1.1.1.1.53: UDP, length 40\n"
        b"2.1 IP6 2001:db8::2.42430 > 2606:4700:4700::1111.443: Flags [S], length 0\n"
    )

    def fake_popen(_command: Sequence[str], **_kwargs: object) -> PipeProcess:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, output)
        return PipeProcess(os.fdopen(read_fd, "rb", buffering=0), write_fd)

    monkeypatch.setattr(audit.selectors, "DefaultSelector", ReadyOnceSelector)
    monkeypatch.setattr(audit.shutil, "which", lambda _: "/usr/sbin/tcpdump")
    monkeypatch.setattr(audit.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(audit, "_proc_fd_available", lambda: True)

    report = audit.capture(
        interface="br-i028",
        clients={CLIENT4, CLIENT6},
        endpoint=ENDPOINT,
        endpoint_ports=(51901, 51902),
        phase="excluded",
        duration=30,
        max_packets=100,
        wait_for_excluded_probe=True,
        start_timeout=10,
    )

    assert report["phase_scope"] == "after_excluded_probe_trigger"
    assert report["excluded_probe_trigger_seen"] is True
    assert report["probe_dns_source_port_packets"] == 1
    assert report["probe_baseline_ipv6_https_source_port_packets"] == 1
    assert report["packet_count"] == 2
    assert report["vpn_outer_endpoint_packets"] == 0
    assert report["packet_limit_reached"] is False
    assert "192.0.2.10" not in repr(report)
    for field in (
        "capture_started_at_utc",
        "capture_completed_at_utc",
        "excluded_probe_trigger_at_utc",
    ):
        assert isinstance(report[field], str)
        assert datetime.fromisoformat(report[field]).utcoffset() is not None


def test_capture_triggered_mode_reports_only_post_trigger_aggregates_and_utc_times(
    monkeypatch,
) -> None:
    class ReadyOnceSelector:
        def __init__(self) -> None:
            self.calls = 0

        def register(self, *_args: object) -> None:
            pass

        def select(self, _timeout: float) -> list[tuple[None, None]]:
            self.calls += 1
            return [(None, None)] if self.calls == 1 else []

        def close(self) -> None:
            pass

    class PipeProcess:
        def __init__(self, stream) -> None:
            self.returncode = None
            self.stdout = stream

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            return int(self.returncode or 0)

    output = (
        b"0.0 IP6 2001:db8::2.50000 > 2606:4700:4700::1111.443: UDP, length 88\n"
        b"1.0 IP 192.0.2.10.50000 > 198.51.100.20.51901: UDP, length 1200\n"
        b"1.1 IP 192.0.2.10.54321 > 1.1.1.1.53: UDP, length 40\n"
        b"1.2 IP6 2001:db8::2.50001 > 2606:4700:4700::1111.443: UDP, length 88\n"
    )
    spawned: list[PipeProcess] = []

    def fake_popen(_command: Sequence[str], **_kwargs: object) -> PipeProcess:
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, output)
        finally:
            os.close(write_fd)
        process = PipeProcess(os.fdopen(read_fd, "rb", buffering=0))
        spawned.append(process)
        return process

    monkeypatch.setattr(audit.selectors, "DefaultSelector", ReadyOnceSelector)
    monkeypatch.setattr(audit.shutil, "which", lambda _: "/usr/sbin/tcpdump")
    monkeypatch.setattr(audit.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(audit, "_proc_fd_available", lambda: True)

    report = audit.capture(
        interface="br-i028",
        clients={CLIENT4, CLIENT6},
        endpoint=ENDPOINT,
        endpoint_ports=(51901, 51902),
        phase="included",
        duration=30,
        max_packets=100,
        wait_for_vpn=True,
        start_timeout=10,
    )

    assert report["phase_scope"] == "after_vpn_outer_trigger"
    assert report["vpn_outer_trigger_seen"] is True
    assert report["vpn_outer_endpoint_packets"] == 1
    assert report["direct_dns_v4_packets"] == 1
    assert report["global_ipv6_egress_packets"] == 1
    assert report["packet_count"] == 3
    assert report["bounded_window_ended"] is True
    assert report["packet_limit_reached"] is False
    assert report["timestamp_clock"] == "capture_host_utc_wall_clock"
    for field in (
        "capture_started_at_utc",
        "capture_completed_at_utc",
        "vpn_outer_trigger_at_utc",
    ):
        assert isinstance(report[field], str)
        assert datetime.fromisoformat(report[field]).utcoffset() is not None
    assert spawned[0].returncode == -9


def test_capture_filter_requires_explicit_client_addresses_and_excludes_link_local() -> None:
    value = audit.build_filter({CLIENT4, CLIENT6}, ENDPOINT, (51901, 51902))
    assert "src host 192.0.2.10" in value
    assert "src host 2001:db8::2" in value
    assert "udp port 53" in value and "tcp port 53" in value
    assert "dst net 2000::/3" in value
    assert "udp and dst host 198.51.100.20 and (dst port 51901 or dst port 51902)" in value
    assert f"udp and src port {audit.PROBE_DNS_SOURCE_PORT}" in value
    assert f"src port {audit.PROBE_IPV6_HTTPS_SOURCE_PORT}" in value
    assert f"src port {audit.PROBE_BASELINE_IPV6_HTTPS_SOURCE_PORT}" in value
    assert "51892" not in value
    assert "not dst net fe80::/10" in value


def test_capture_filter_can_target_one_ap_station_without_ip_addresses() -> None:
    station = "60:83:e7:cb:1a:29"
    value = audit.build_filter(None, ENDPOINT, (51901, 51902), client_mac=station)
    assert f"ether src host {station}" in value
    assert "udp port 53" in value and "tcp port 53" in value
    assert "dst net 2000::/3" in value
    assert "udp and dst host 198.51.100.20 and (dst port 51901 or dst port 51902)" in value
    assert f"udp and src port {audit.PROBE_DNS_SOURCE_PORT}" in value
    assert f"src port {audit.PROBE_IPV6_HTTPS_SOURCE_PORT}" in value
    assert f"src port {audit.PROBE_BASELINE_IPV6_HTTPS_SOURCE_PORT}" in value


def test_probe_source_ports_are_counted_without_retaining_addresses() -> None:
    report = audit.summarize_headers(
        [
            "1.0 IP 192.0.2.10.42428 > 1.1.1.1.53: UDP, length 40",
            "1.1 IP6 2001:db8::2.42429 > 2606:4700:4700::1111.443: Flags [S], length 0",
            "1.2 IP6 2001:db8::2.42430 > 2606:4700:4700::1111.443: Flags [S], length 0",
            "1.3 IP6 2001:db8::2.50000 > 2606:4700:4700::1111.443: Flags [S], length 0",
        ],
        clients={CLIENT4, CLIENT6},
        vpn_endpoint=ENDPOINT,
        vpn_endpoint_ports=audit.DEFAULT_VPN_ENDPOINT_PORTS,
        phase="included",
    )

    assert report["probe_dns_source_port_packets"] == 1
    assert report["probe_ipv6_https_source_port_packets"] == 1
    assert report["probe_baseline_ipv6_https_source_port_packets"] == 1
    assert "192.0.2.10" not in repr(report)
    assert "2606:4700" not in repr(report)


def test_ap_station_discovery_returns_only_the_single_station_without_printing_it(
    monkeypatch,
) -> None:
    station = "60:83:e7:cb:1a:29"
    monkeypatch.setattr(audit.shutil, "which", lambda _: "/usr/sbin/iw")
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=f"Station {station.upper()} (on wlx6083e7cb1a29)\n"
        ),
    )

    assert audit.discover_ap_client_mac("wlx6083e7cb1a29") == station


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Station 60:83:e7:cb:1a:29 (on wlx0)\nStation 00:11:22:33:44:55 (on wlx0)\n",
    ],
)
def test_ap_station_discovery_fails_closed_without_exactly_one_station(
    monkeypatch, output: str
) -> None:
    monkeypatch.setattr(audit.shutil, "which", lambda _: "/usr/sbin/iw")
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )
    with pytest.raises(audit.AuditError, match="ap_station_count_invalid"):
        audit.discover_ap_client_mac("wlx0")


def test_auto_ap_cli_needs_no_ip_or_endpoint_prompts(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_capture(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"test_only": True, "phase": "included", "packet_count": 0}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "i028_packet_audit.py",
            "--interface",
            "br-i028",
            "--phase",
            "included",
            "--ap-interface",
            "wlx6083e7cb1a29",
        ],
    )
    monkeypatch.setattr(audit, "discover_ap_client_mac", lambda _: "60:83:e7:cb:1a:29")
    monkeypatch.setattr(audit, "capture", fake_capture)
    monkeypatch.setattr(
        audit.getpass,
        "getpass",
        lambda _: pytest.fail("auto AP mode must not prompt for addresses or endpoint"),
    )

    assert audit.main() == 0
    assert captured["clients"] is None
    assert captured["client_mac"] == "60:83:e7:cb:1a:29"
    assert captured["endpoint"] == audit.DEFAULT_VPN_ENDPOINT
    assert captured["endpoint_ports"] == audit.DEFAULT_VPN_ENDPOINT_PORTS
    assert "60:83:e7:cb:1a:29" not in capsys.readouterr().out


def test_cli_reports_only_fixed_audit_error_code(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "i028_packet_audit.py",
            "--interface",
            "br-i028",
            "--phase",
            "included",
            "--ap-interface",
            "wlx6083e7cb1a29",
        ],
    )

    def fail_discovery(_: str) -> str:
        raise audit.AuditError("ap_station_count_invalid")

    monkeypatch.setattr(audit, "discover_ap_client_mac", fail_discovery)
    assert audit.main() == 2
    assert capsys.readouterr().out == '{"code": "ap_station_count_invalid"}\n'


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
        endpoint_ports=51820,
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
    assert "udp and dst host 198.51.100.20 and (dst port 51820)" in filters[0]
    assert "192.0.2.10" not in repr(spawned[0].command)
    assert "198.51.100.20" not in repr(spawned[0].command)


def test_capture_uses_temporary_mac_filter_and_reports_only_aggregates(monkeypatch) -> None:
    spawned: list[_TestOnlyProcess] = []
    filters: list[str] = []
    station = "60:83:e7:cb:1a:29"

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
        interface="br-i028",
        clients=None,
        endpoint=ENDPOINT,
        endpoint_ports=51820,
        phase="included",
        duration=10,
        max_packets=100,
        client_mac=station,
    )

    assert report["packet_count"] == 1
    assert report["client_filter_mode"] == "ap_station"
    assert station in filters[0]
    assert station not in repr(report)
    assert station not in repr(spawned[0].command)


def test_capture_rejects_unbounded_or_shell_like_inputs() -> None:
    with pytest.raises(audit.AuditError, match="interface_invalid"):
        audit.capture(
            interface="wlan0;rm",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            endpoint_ports=51820,
            phase="included",
            duration=10,
            max_packets=100,
        )
    with pytest.raises(audit.AuditError, match="duration_invalid"):
        audit.capture(
            interface="wlan0",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            endpoint_ports=51820,
            phase="included",
            duration=181,
            max_packets=100,
        )
    with pytest.raises(audit.AuditError, match="packet_limit_invalid"):
        audit.capture(
            interface="wlan0",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            endpoint_ports=51820,
            phase="included",
            duration=10,
            max_packets=20001,
        )


def test_capture_rejects_invalid_endpoint_port() -> None:
    with pytest.raises(audit.AuditError, match="vpn_endpoint_port_invalid"):
        audit.capture(
            interface="wlan0",
            clients={CLIENT4},
            endpoint=ENDPOINT,
            endpoint_ports=65536,
            phase="included",
            duration=10,
            max_packets=100,
        )


def test_endpoint_trigger_mode_is_included_only_and_start_wait_is_bounded() -> None:
    common = {
        "interface": "wlan0",
        "clients": {CLIENT4},
        "endpoint": ENDPOINT,
        "endpoint_ports": (51901, 51902),
        "duration": 10,
        "max_packets": 100,
        "wait_for_vpn": True,
    }
    with pytest.raises(audit.AuditError, match="capture_trigger_phase_invalid"):
        audit.capture(**common, phase="excluded")
    with pytest.raises(audit.AuditError, match="capture_trigger_timeout_invalid"):
        audit.capture(**common, phase="included", start_timeout=301)

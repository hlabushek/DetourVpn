"""Bounded, payload-free I-028 packet header counters for a controlled test LAN."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_DURATION_SECONDS = 180
MAX_TRIGGER_WAIT_SECONDS = 300
MAX_PACKETS = 20000
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
GLOBAL_V6 = ipaddress.ip_network("2000::/3")
GLOBAL_V6_PROTOCOLS = ("udp", "tcp", "icmp", "other")
GLOBAL_V6_PORT_CATEGORIES = ("port_53", "port_443", "other_port", "no_transport_port")
LINE = re.compile(r"^\S+\s+(IP6?)\s+(\S+)\s+>\s+(\S+):\s+(.+)$")
INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MAC_ADDRESS = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
DEFAULT_VPN_ENDPOINT = ipaddress.ip_address("45.10.166.106")
# I-028 A has two slots; runtime.py sets AWG ListenPort to 51900 + slot_id.
DEFAULT_VPN_ENDPOINT_PORTS = (51901, 51902)
# Test-only source ports used by I028Probe; counters retain no addresses or headers.
PROBE_DNS_SOURCE_PORT = 42428
PROBE_IPV6_HTTPS_SOURCE_PORT = 42429
PROBE_BASELINE_IPV6_HTTPS_SOURCE_PORT = 42430


class AuditError(ValueError):
    """A stable, non-sensitive packet audit error."""


def validate_endpoint_ports(value: int | tuple[int, ...]) -> tuple[int, ...]:
    ports = (value,) if type(value) is int else value
    if (
        type(ports) is not tuple
        or not ports
        or len(ports) > 16
        or any(type(port) is not int or not 1 <= port <= 65535 for port in ports)
        or len(set(ports)) != len(ports)
    ):
        raise AuditError("vpn_endpoint_port_invalid")
    return tuple(sorted(ports))


def _proc_fd_available() -> bool:
    return os.name == "posix" and Path("/proc/self/fd").is_dir()


@dataclass(frozen=True)
class Packet:
    family: int
    source: ipaddress.IPv4Address | ipaddress.IPv6Address
    destination: ipaddress.IPv4Address | ipaddress.IPv6Address
    source_port: int | None
    destination_port: int | None
    protocol: str


def _endpoint(
    token: str, family: int
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, int | None]:
    value = token.rstrip(",")
    port: int | None = None
    if "." in value:
        candidate, suffix = value.rsplit(".", 1)
        if suffix.isdecimal() and 0 <= int(suffix) <= 65535:
            value, port = candidate, int(suffix)
    if "%" in value:
        value = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise AuditError("capture_parse_failed") from exc
    if address.version != family:
        raise AuditError("capture_parse_failed")
    return address, port


def parse_packet_line(line: str) -> Packet:
    """Parse one numeric tcpdump header line; reject hostnames and payload text."""
    match = LINE.fullmatch(line.strip())
    if match is None:
        raise AuditError("capture_parse_failed")
    if "truncated" in match.group(4).lower():
        raise AuditError("capture_parse_failed")
    family = 6 if match.group(1) == "IP6" else 4
    source, source_port = _endpoint(match.group(2), family)
    destination, port = _endpoint(match.group(3), family)
    description = match.group(4)
    protocol = (
        "udp"
        if re.match(r"UDP(?:,|\s|$)", description, re.IGNORECASE)
        else "tcp"
        if re.match(r"(?:Flags\s+\[|TCP(?:,|\s|$))", description, re.IGNORECASE)
        else "icmp"
        if re.match(r"ICMP6?(?:,|\s|$)", description, re.IGNORECASE)
        else "other"
    )
    return Packet(family, source, destination, source_port, port, protocol)


def summarize_headers(
    lines: Iterable[str],
    *,
    clients: set[ipaddress.IPv4Address | ipaddress.IPv6Address] | None,
    vpn_endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    vpn_endpoint_ports: int | tuple[int, ...],
    phase: str,
) -> dict[str, object]:
    if phase not in {"baseline", "included", "excluded", "transition", "outage"}:
        raise AuditError("phase_invalid")
    endpoint_ports = validate_endpoint_ports(vpn_endpoint_ports)
    counts: dict[str, int] = {
        "packet_count": 0,
        "direct_dns_v4_packets": 0,
        "direct_dns_v6_packets": 0,
        "global_ipv6_egress_packets": 0,
        "vpn_outer_endpoint_packets": 0,
        "direct_dns_v4_udp_egress_packets": 0,
        "direct_dns_v4_tcp_egress_packets": 0,
        "direct_dns_v6_udp_egress_packets": 0,
        "direct_dns_v6_tcp_egress_packets": 0,
        "probe_dns_source_port_packets": 0,
        "probe_ipv6_https_source_port_packets": 0,
        "probe_baseline_ipv6_https_source_port_packets": 0,
        "global_ipv6_egress_udp_packets": 0,
        "global_ipv6_egress_tcp_packets": 0,
        "global_ipv6_egress_icmp_packets": 0,
        "global_ipv6_egress_other_packets": 0,
        "global_ipv6_egress_port_53_packets": 0,
        "global_ipv6_egress_port_443_packets": 0,
        "global_ipv6_egress_other_port_packets": 0,
        "global_ipv6_egress_no_transport_port_packets": 0,
        "vpn_outer_endpoint_udp_egress_packets": 0,
        "vpn_outer_endpoint_tcp_egress_packets": 0,
        "vpn_outer_endpoint_other_egress_packets": 0,
    }
    counts.update(
        {
            f"global_ipv6_egress_{protocol}_{port_category}_packets": 0
            for protocol in GLOBAL_V6_PROTOCOLS
            for port_category in GLOBAL_V6_PORT_CATEGORIES
        }
    )
    for line in lines:
        if not line.strip():
            continue
        packet = parse_packet_line(line)
        if clients is not None and packet.source not in clients:
            continue
        counts["packet_count"] += 1
        if is_vpn_outer_packet(packet, vpn_endpoint, endpoint_ports):
            counts["vpn_outer_endpoint_packets"] += 1
            protocol_key = (
                "udp"
                if packet.protocol == "udp"
                else "tcp"
                if packet.protocol == "tcp"
                else "other"
            )
            counts[f"vpn_outer_endpoint_{protocol_key}_egress_packets"] += 1
            continue
        if packet.source_port == PROBE_DNS_SOURCE_PORT and packet.protocol == "udp":
            counts["probe_dns_source_port_packets"] += 1
        if (
            packet.family == 6
            and packet.protocol == "tcp"
            and packet.source_port == PROBE_IPV6_HTTPS_SOURCE_PORT
        ):
            counts["probe_ipv6_https_source_port_packets"] += 1
        if (
            packet.family == 6
            and packet.protocol == "tcp"
            and packet.source_port == PROBE_BASELINE_IPV6_HTTPS_SOURCE_PORT
        ):
            counts["probe_baseline_ipv6_https_source_port_packets"] += 1
        if (
            packet.destination_port == 53
            and packet.protocol in {"udp", "tcp"}
            and not packet.destination.is_multicast
            and not packet.destination.is_link_local
            and not packet.destination.is_unspecified
            and not packet.destination.is_loopback
        ):
            key = "direct_dns_v4_packets" if packet.family == 4 else "direct_dns_v6_packets"
            counts[key] += 1
            counts[f"direct_dns_v{packet.family}_{packet.protocol}_egress_packets"] += 1
        if (
            isinstance(packet.destination, ipaddress.IPv6Address)
            and packet.destination in GLOBAL_V6
        ):
            counts["global_ipv6_egress_packets"] += 1
            protocol_key = packet.protocol if packet.protocol in {"udp", "tcp", "icmp"} else "other"
            counts[f"global_ipv6_egress_{protocol_key}_packets"] += 1
            port_key = (
                "port_53"
                if packet.destination_port == 53
                else "port_443"
                if packet.destination_port == 443
                else "other_port"
                if packet.destination_port is not None
                else "no_transport_port"
            )
            counts[f"global_ipv6_egress_{port_key}_packets"] += 1
            counts[f"global_ipv6_egress_{protocol_key}_{port_key}_packets"] += 1
    # Only aggregate counters leave this function; addresses and lines are discarded.
    return {
        "test_only": True,
        "phase": phase,
        "direction": "egress",
        "client_filter_mode": "ap_station" if clients is None else "explicit_addresses",
        "vpn_outer_endpoint_ports": list(endpoint_ports),
        "phase_scope": "operator_labeled_window",
        **counts,
    }


def is_vpn_outer_packet(
    packet: Packet,
    vpn_endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    vpn_endpoint_ports: int | tuple[int, ...],
) -> bool:
    """Recognize only the fixed outgoing UDP endpoint packets for I-028 A."""
    return (
        packet.protocol == "udp"
        and packet.destination == vpn_endpoint
        and packet.destination_port in validate_endpoint_ports(vpn_endpoint_ports)
    )


def is_excluded_probe_packet(packet: Packet) -> bool:
    """Recognize only the fixed direct DNS/IPv6 positive-control flows."""
    return (
        packet.protocol == "udp" and packet.source_port == PROBE_DNS_SOURCE_PORT
    ) or (
        packet.family == 6
        and packet.protocol == "tcp"
        and packet.source_port == PROBE_BASELINE_IPV6_HTTPS_SOURCE_PORT
    )


def collect_headers_after_trigger(
    stream,
    *,
    vpn_endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    vpn_endpoint_ports: int | tuple[int, ...],
    duration: int,
    start_timeout: int,
    trigger_mode: str,
) -> tuple[list[str], float, str, bool, bool]:
    """Discard pre-trigger headers, then collect one bounded observation window.

    Returns captured headers, trigger wait seconds, window-ended, and packet-limit
    status. Raw pre-trigger headers are parsed and immediately discarded.
    """
    if trigger_mode not in {"vpn_outer", "excluded_probe"}:
        raise AuditError("capture_trigger_invalid")
    start = time.monotonic()
    trigger_deadline = start + start_timeout
    capture_deadline: float | None = None
    trigger_wait: float | None = None
    trigger_at_utc: str | None = None
    headers: list[str] = []
    output_bytes = 0
    pending = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)

    def require_trigger() -> tuple[float, str]:
        if trigger_wait is None or trigger_at_utc is None:
            raise AuditError("capture_trigger_not_seen")
        return trigger_wait, trigger_at_utc

    try:
        while True:
            now = time.monotonic()
            deadline = capture_deadline if capture_deadline is not None else trigger_deadline
            timeout = deadline - now
            if timeout <= 0:
                wait_seconds, trigger_utc = require_trigger()
                return headers, wait_seconds, trigger_utc, True, False

            ready = selector.select(timeout)
            if not ready:
                wait_seconds, trigger_utc = require_trigger()
                return headers, wait_seconds, trigger_utc, True, False

            raw_chunk = os.read(stream.fileno(), min(65536, MAX_OUTPUT_BYTES + 1 - output_bytes))
            if not raw_chunk:
                # tcpdump exits successfully when its fixed packet limit is reached.
                if pending:
                    raise AuditError("capture_parse_failed")
                wait_seconds, trigger_utc = require_trigger()
                return headers, wait_seconds, trigger_utc, False, True
            output_bytes += len(raw_chunk)
            if output_bytes > MAX_OUTPUT_BYTES:
                raise AuditError("capture_output_limit")
            pending.extend(raw_chunk)
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    break
                raw_line = bytes(pending[: newline + 1])
                del pending[: newline + 1]
                try:
                    line = raw_line.decode("ascii", errors="strict")
                except UnicodeDecodeError as exc:
                    raise AuditError("capture_parse_failed") from exc

                if trigger_wait is None:
                    packet = parse_packet_line(line)
                    matched = (
                        is_vpn_outer_packet(packet, vpn_endpoint, vpn_endpoint_ports)
                        if trigger_mode == "vpn_outer"
                        else is_excluded_probe_packet(packet)
                    )
                    if matched:
                        trigger_wait = round(time.monotonic() - start, 2)
                        trigger_at_utc = datetime.now(UTC).isoformat()
                        capture_deadline = time.monotonic() + duration
                        headers.append(line)
                    continue
                headers.append(line)
    finally:
        selector.close()


def collect_headers_after_vpn_trigger(
    stream,
    *,
    vpn_endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    vpn_endpoint_ports: int | tuple[int, ...],
    duration: int,
    start_timeout: int,
) -> tuple[list[str], float, str, bool, bool]:
    """Compatibility wrapper for the included-phase outer-VPN trigger."""
    return collect_headers_after_trigger(
        stream,
        vpn_endpoint=vpn_endpoint,
        vpn_endpoint_ports=vpn_endpoint_ports,
        duration=duration,
        start_timeout=start_timeout,
        trigger_mode="vpn_outer",
    )


def build_filter(
    clients: set[ipaddress.IPv4Address | ipaddress.IPv6Address] | None,
    endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    endpoint_ports: int | tuple[int, ...],
    *,
    client_mac: str | None = None,
) -> str:
    if (clients is None) == (client_mac is None):
        raise AuditError("client_address_invalid")
    if clients is not None and (
        not clients
        or any(
            type(client) not in (ipaddress.IPv4Address, ipaddress.IPv6Address) for client in clients
        )
    ):
        raise AuditError("client_address_invalid")
    if client_mac is not None and not MAC_ADDRESS.fullmatch(client_mac):
        raise AuditError("client_address_invalid")
    ports = validate_endpoint_ports(endpoint_ports)
    source = (
        f"ether src host {client_mac.lower()}"
        if client_mac is not None
        else " or ".join(
            f"src host {client.compressed}" for client in sorted(clients or (), key=str)
        )
    )
    port_filter = " or ".join(f"dst port {port}" for port in ports)
    destination = (
        "((ip and (udp port 53 or tcp port 53)) or "
        "(ip6 and (udp port 53 or tcp port 53) and "
        "not dst net fe80::/10 and not dst net ff00::/8) or "
        f"(udp and src port {PROBE_DNS_SOURCE_PORT}) or "
        f"(ip6 and tcp and (src port {PROBE_IPV6_HTTPS_SOURCE_PORT} or "
        f"src port {PROBE_BASELINE_IPV6_HTTPS_SOURCE_PORT})) or "
        f"(udp and dst host {endpoint.compressed} and ({port_filter})) or "
        f"(ip6 and dst net {GLOBAL_V6}))"
    )
    return f"({source}) and {destination}"


def capture(
    *,
    interface: str,
    clients: set[ipaddress.IPv4Address | ipaddress.IPv6Address] | None,
    endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    endpoint_ports: int | tuple[int, ...],
    phase: str,
    duration: int,
    max_packets: int,
    client_mac: str | None = None,
    wait_for_vpn: bool = False,
    wait_for_excluded_probe: bool = False,
    start_timeout: int = 240,
    tcpdump: str = "tcpdump",
) -> dict[str, object]:
    if not INTERFACE.fullmatch(interface):
        raise AuditError("interface_invalid")
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_SECONDS:
        raise AuditError("duration_invalid")
    if type(max_packets) is not int or not 1 <= max_packets <= MAX_PACKETS:
        raise AuditError("packet_limit_invalid")
    if phase not in {"baseline", "included", "excluded", "transition", "outage"}:
        raise AuditError("phase_invalid")
    if type(wait_for_vpn) is not bool or type(wait_for_excluded_probe) is not bool:
        raise AuditError("capture_trigger_invalid")
    if wait_for_vpn and phase != "included":
        raise AuditError("capture_trigger_phase_invalid")
    if wait_for_excluded_probe and phase != "excluded":
        raise AuditError("capture_trigger_phase_invalid")
    if wait_for_vpn and wait_for_excluded_probe:
        raise AuditError("capture_trigger_invalid")
    if (wait_for_vpn or wait_for_excluded_probe) and (
        type(start_timeout) is not int or not 1 <= start_timeout <= MAX_TRIGGER_WAIT_SECONDS
    ):
        raise AuditError("capture_trigger_timeout_invalid")
    ports = validate_endpoint_ports(endpoint_ports)
    executable = shutil.which(tcpdump)
    if executable is None:
        raise AuditError("tcpdump_unavailable")
    if not _proc_fd_available():
        raise AuditError("capture_platform_unsupported")
    capture_started_at_utc = datetime.now(UTC).isoformat()
    process: subprocess.Popen[bytes] | None = None
    try:
        # Keep device/VPN addresses out of process arguments and persistent files.
        with tempfile.TemporaryFile(mode="w+t", encoding="ascii") as filter_file:
            filter_file.write(build_filter(clients, endpoint, ports, client_mac=client_mac))
            filter_file.flush()
            filter_file.seek(0)
            filter_fd = filter_file.fileno()
            command = [
                executable,
                "-nn",
                "-q",
                "-l",
                "-tt",
                "-s",
                "256",
                "-i",
                interface,
                "-c",
                str(max_packets),
                "-F",
                f"/proc/self/fd/{filter_fd}",
            ]
            process = subprocess.Popen(  # noqa: S603 - fixed executable and validated args
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                pass_fds=(filter_fd,),
                bufsize=0,
            )
            if wait_for_vpn or wait_for_excluded_probe:
                headers, trigger_wait, trigger_at_utc, timed_out, packet_limit_ended = (
                    collect_headers_after_trigger(
                        process.stdout,
                        vpn_endpoint=endpoint,
                        vpn_endpoint_ports=ports,
                        duration=duration,
                        start_timeout=start_timeout,
                        trigger_mode=("vpn_outer" if wait_for_vpn else "excluded_probe"),
                    )
                )
                if packet_limit_ended:
                    process.wait(timeout=5)
                output = "".join(headers).encode("ascii")
            else:
                try:
                    output, _ = process.communicate(timeout=duration)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()
                    timed_out = True
        if len(output) > MAX_OUTPUT_BYTES:
            raise AuditError("capture_output_limit")
        if not timed_out and process.poll() != 0:
            raise AuditError("capture_failed")
        decoded = output.decode("ascii", errors="strict")
        report = summarize_headers(
            decoded.splitlines(),
            clients=clients,
            vpn_endpoint=endpoint,
            vpn_endpoint_ports=ports,
            phase=phase,
        )
        report.update(
            {
                "duration_seconds": duration,
                "bounded_window_ended": timed_out,
                "packet_limit": max_packets,
                "capture_started_at_utc": capture_started_at_utc,
                "capture_completed_at_utc": datetime.now(UTC).isoformat(),
                "timestamp_clock": "capture_host_utc_wall_clock",
            }
        )
        if wait_for_vpn:
            report.update(
                {
                    "phase_scope": "after_vpn_outer_trigger",
                    "vpn_outer_trigger_seen": True,
                    "trigger_wait_seconds": trigger_wait,
                    "vpn_outer_trigger_at_utc": trigger_at_utc,
                }
            )
        elif wait_for_excluded_probe:
            report.update(
                {
                    "phase_scope": "after_excluded_probe_trigger",
                    "excluded_probe_trigger_seen": True,
                    "trigger_wait_seconds": trigger_wait,
                    "excluded_probe_trigger_at_utc": trigger_at_utc,
                }
            )
        packet_count = report["packet_count"]
        if type(packet_count) is not int:
            raise AuditError("capture_parse_failed")
        packet_limit_reached = packet_count >= max_packets
        if (wait_for_vpn or wait_for_excluded_probe) and packet_limit_ended:
            packet_limit_reached = True
        report["packet_limit_reached"] = packet_limit_reached
        if packet_limit_reached:
            report["bounded_window_ended"] = False
        return report
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError) as exc:
        raise AuditError("capture_failed") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process is not None and process.stdout is not None:
            close_stdout = getattr(process.stdout, "close", None)
            if close_stdout is not None:
                close_stdout()


def discover_ap_client_mac(interface: str) -> str:
    """Return the only associated station MAC without logging or persisting it."""
    if not INTERFACE.fullmatch(interface):
        raise AuditError("interface_invalid")
    executable = shutil.which("iw")
    if executable is None:
        raise AuditError("ap_station_discovery_failed")
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and validated interface
            [executable, "dev", interface, "station", "dump"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuditError("ap_station_discovery_failed") from exc
    if result.returncode != 0:
        raise AuditError("ap_station_discovery_failed")
    stations = re.findall(
        r"(?m)^Station\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})(?:\s|$)",
        result.stdout,
    )
    if len(stations) != 1:
        raise AuditError("ap_station_count_invalid")
    return stations[0].lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument(
        "--ap-interface",
        help="Auto-select the sole associated AP station; addresses and endpoint stay local.",
    )
    parser.add_argument(
        "--phase",
        choices=("baseline", "included", "excluded", "transition", "outage"),
        required=True,
    )
    parser.add_argument("--duration", type=int, default=90)
    # The trigger wait itself can consume hundreds of matching packets before
    # the VPN starts. Keep enough headroom for a full bounded observation after
    # the trigger while retaining a hard memory/output ceiling.
    parser.add_argument("--max-packets", type=int, default=20000)
    parser.add_argument(
        "--wait-for-vpn",
        action="store_true",
        help="For included phase, start counting after the fixed VPN UDP endpoint is observed.",
    )
    parser.add_argument(
        "--wait-for-excluded-probe",
        action="store_true",
        help="For excluded phase, start counting after fixed direct DNS or IPv6 probe traffic.",
    )
    parser.add_argument(
        "--start-timeout",
        type=int,
        default=240,
        help="Maximum seconds to wait for the selected fixed trigger packet.",
    )
    args = parser.parse_args()
    try:
        client_mac: str | None = None
        if args.ap_interface:
            clients = None
            client_mac = discover_ap_client_mac(args.ap_interface)
            endpoint = DEFAULT_VPN_ENDPOINT
            endpoint_ports = DEFAULT_VPN_ENDPOINT_PORTS
        else:
            clients = {
                ipaddress.ip_address(value)
                for value in (
                    getpass.getpass("Test phone IPv4 (hidden; empty if unavailable): "),
                    getpass.getpass("Test phone IPv6 (hidden; empty if unavailable): "),
                )
                if value
            }
            endpoint = ipaddress.ip_address(getpass.getpass("VPN endpoint IP (hidden): "))
            endpoint_port_text = getpass.getpass("VPN endpoint UDP port (hidden): ")
            if not endpoint_port_text.isdecimal():
                raise AuditError("vpn_endpoint_port_invalid")
            endpoint_ports = (int(endpoint_port_text),)
        report = capture(
            interface=args.interface,
            clients=clients,
            endpoint=endpoint,
            endpoint_ports=endpoint_ports,
            phase=args.phase,
            duration=args.duration,
            max_packets=args.max_packets,
            client_mac=client_mac,
            wait_for_vpn=args.wait_for_vpn,
            wait_for_excluded_probe=args.wait_for_excluded_probe,
            start_timeout=args.start_timeout,
        )
    except AuditError as exc:
        # AuditError messages are fixed categories; never print command output,
        # captured headers, addresses, or station identifiers here.
        print(json.dumps({"code": str(exc)}, sort_keys=True))
        return 2
    except ValueError:
        print('{"code":"packet_audit_failed"}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

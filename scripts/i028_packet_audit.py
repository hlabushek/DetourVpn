"""Bounded, payload-free I-028 packet header counters for a controlled test LAN."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_DURATION_SECONDS = 180
MAX_PACKETS = 5000
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
GLOBAL_V6 = ipaddress.ip_network("2000::/3")
LINE = re.compile(r"^\S+\s+(IP6?)\s+(\S+)\s+>\s+(\S+):\s+(.+)$")
INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class AuditError(ValueError):
    """A stable, non-sensitive packet audit error."""


def _proc_fd_available() -> bool:
    return os.name == "posix" and Path("/proc/self/fd").is_dir()


@dataclass(frozen=True)
class Packet:
    family: int
    source: ipaddress.IPv4Address | ipaddress.IPv6Address
    destination: ipaddress.IPv4Address | ipaddress.IPv6Address
    destination_port: int | None
    protocol: str


def _endpoint(token: str, family: int) -> tuple[ipaddress._BaseAddress, int | None]:
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
    source, _ = _endpoint(match.group(2), family)
    destination, port = _endpoint(match.group(3), family)
    description = match.group(4)
    protocol = (
        "udp"
        if re.match(r"UDP(?:,|\s|$)", description, re.IGNORECASE)
        else "tcp"
        if re.match(r"(?:Flags\s+\[|TCP(?:,|\s|$))", description, re.IGNORECASE)
        else "other"
    )
    return Packet(family, source, destination, port, protocol)


def summarize_headers(
    lines: Iterable[str],
    *,
    clients: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    vpn_endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    phase: str,
) -> dict[str, object]:
    if phase not in {"baseline", "included", "excluded", "transition", "outage"}:
        raise AuditError("phase_invalid")
    counts = {
        "packet_count": 0,
        "direct_dns_v4_packets": 0,
        "direct_dns_v6_packets": 0,
        "global_ipv6_egress_packets": 0,
        "vpn_outer_endpoint_packets": 0,
    }
    for line in lines:
        if not line.strip():
            continue
        packet = parse_packet_line(line)
        if packet.source not in clients:
            continue
        counts["packet_count"] += 1
        if packet.destination == vpn_endpoint:
            counts["vpn_outer_endpoint_packets"] += 1
            continue
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
        if (
            isinstance(packet.destination, ipaddress.IPv6Address)
            and packet.destination in GLOBAL_V6
        ):
            counts["global_ipv6_egress_packets"] += 1
    # Only aggregate counters leave this function; addresses and lines are discarded.
    return {"test_only": True, "phase": phase, **counts}


def build_filter(
    clients: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str:
    if not clients or any(
        type(client) not in (ipaddress.IPv4Address, ipaddress.IPv6Address) for client in clients
    ):
        raise AuditError("client_address_invalid")
    source = " or ".join(f"src host {client.compressed}" for client in sorted(clients, key=str))
    destination = (
        "((ip and (udp port 53 or tcp port 53)) or "
        "(ip6 and (udp port 53 or tcp port 53) and "
        "not dst net fe80::/10 and not dst net ff00::/8) or "
        f"(dst host {endpoint.compressed}) or (ip6 and dst net {GLOBAL_V6}))"
    )
    return f"({source}) and {destination}"


def capture(
    *,
    interface: str,
    clients: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    endpoint: ipaddress.IPv4Address | ipaddress.IPv6Address,
    phase: str,
    duration: int,
    max_packets: int,
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
    executable = shutil.which(tcpdump)
    if executable is None:
        raise AuditError("tcpdump_unavailable")
    if not _proc_fd_available():
        raise AuditError("capture_platform_unsupported")
    process: subprocess.Popen[bytes] | None = None
    try:
        # Keep device/VPN addresses out of process arguments and persistent files.
        with tempfile.TemporaryFile(mode="w+t", encoding="ascii") as filter_file:
            filter_file.write(build_filter(clients, endpoint))
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
            )
            try:
                output, _ = process.communicate(timeout=duration)
                timed_out = False
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                timed_out = True
        if len(output) > MAX_OUTPUT_BYTES:
            raise AuditError("capture_output_limit")
        if not timed_out and process.returncode != 0:
            raise AuditError("capture_failed")
        decoded = output.decode("ascii", errors="strict")
        report = summarize_headers(
            decoded.splitlines(), clients=clients, vpn_endpoint=endpoint, phase=phase
        )
        report.update(
            {
                "duration_seconds": duration,
                "bounded_window_ended": timed_out,
                "packet_limit": max_packets,
                "packet_limit_reached": report["packet_count"] >= max_packets,
            }
        )
        if report["packet_limit_reached"]:
            report["bounded_window_ended"] = False
        return report
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError) as exc:
        raise AuditError("capture_failed") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument(
        "--phase",
        choices=("baseline", "included", "excluded", "transition", "outage"),
        required=True,
    )
    parser.add_argument("--duration", type=int, default=90)
    parser.add_argument("--max-packets", type=int, default=2000)
    args = parser.parse_args()
    try:
        clients = {
            ipaddress.ip_address(value)
            for value in (
                getpass.getpass("Test phone IPv4 (hidden; empty if unavailable): "),
                getpass.getpass("Test phone IPv6 (hidden; empty if unavailable): "),
            )
            if value
        }
        endpoint = ipaddress.ip_address(getpass.getpass("VPN endpoint IP (hidden): "))
        report = capture(
            interface=args.interface,
            clients=clients,
            endpoint=endpoint,
            phase=args.phase,
            duration=args.duration,
            max_packets=args.max_packets,
        )
    except (AuditError, ValueError):
        print('{"code":"packet_audit_failed"}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

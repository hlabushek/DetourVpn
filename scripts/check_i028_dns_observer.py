"""Local-only synthetic namespace lab for the I-028 DNS observer; no real WAN."""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IP = "/usr/sbin/ip"
RESPONDER = (
    "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
    "s.bind(('1.1.1.1',53));q,a=s.recvfrom(512);s.sendto(q,a)"
)
QUERY = (
    "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
    "s.bind(('10.203.71.2',42428));s.settimeout(3);"
    "s.sendto(bytes.fromhex('492801000001000000000000076578616d706c6503636f6d0000010001'),"
    "('1.1.1.1',53));s.recvfrom(512)"
)


def run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=10)  # noqa: S603


def setup_synthetic_network() -> None:
    if os.geteuid() != 0 or any(
        Path(f"/proc/self/ns/{n}").stat().st_ino == Path(f"/proc/1/ns/{n}").stat().st_ino
        for n in ("net", "mnt")
    ):
        raise RuntimeError("private_namespaces_required")
    for ns in ("dv3slot1", "dnsclient", "dnswan"):
        run(IP, "netns", "add", ns)
        run(IP, "-n", ns, "link", "set", "lo", "up")
    for left, right, lns, rns in (
        ("dv3s1", "dnscli", "dv3slot1", "dnsclient"),
        ("outer0", "dnswan0", "dv3slot1", "dnswan"),
    ):
        run(IP, "link", "add", left, "type", "veth", "peer", "name", right)
        run(IP, "link", "set", left, "netns", lns)
        run(IP, "link", "set", right, "netns", rns)
        run(IP, "-n", lns, "link", "set", left, "up")
        run(IP, "-n", rns, "link", "set", right, "up")
    for ns, addr, dev in (
        ("dv3slot1", "10.203.71.1/30", "dv3s1"),
        ("dnsclient", "10.203.71.2/30", "dnscli"),
        ("dv3slot1", "10.203.80.2/30", "outer0"),
        ("dnswan", "10.203.80.1/30", "dnswan0"),
        ("dnswan", "1.1.1.1/32", "lo"),
    ):
        run(IP, "-n", ns, "addr", "add", addr, "dev", dev)
    run(IP, "-n", "dnsclient", "route", "add", "default", "via", "10.203.71.1")
    run(IP, "-n", "dv3slot1", "route", "add", "default", "via", "10.203.80.1")
    run(IP, "netns", "exec", "dv3slot1", "/usr/sbin/sysctl", "-q", "-w", "net.ipv4.ip_forward=1")
    run(
        IP,
        "netns",
        "exec",
        "dv3slot1",
        "/usr/sbin/iptables",
        "-t",
        "nat",
        "-A",
        "POSTROUTING",
        "-o",
        "outer0",
        "-j",
        "MASQUERADE",
    )


def main() -> None:
    if os.geteuid() != 0 or any(
        Path(f"/proc/self/ns/{n}").stat().st_ino == Path(f"/proc/1/ns/{n}").stat().st_ino
        for n in ("net", "mnt")
    ):
        raise RuntimeError("private_namespaces_required")
    run("/usr/bin/mount", "--make-rprivate", "/")
    run("/usr/bin/mount", "-t", "tmpfs", "-o", "mode=0700,nosuid,nodev", "tmpfs", "/run")
    setup_synthetic_network()
    source = ROOT / "infra/staging-v3/dns_observer.py"
    spec = importlib.util.spec_from_file_location("dns_observer", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("observer_unavailable")
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    server = subprocess.Popen(  # noqa: S603 - isolated synthetic endpoint
        [IP, "netns", "exec", "dnswan", sys.executable, "-c", RESPONDER],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    read_fd, write_fd = os.pipe()
    try:
        time.sleep(0.3)
        def send_synthetic_query() -> None:
            run(IP, "netns", "exec", "dnsclient", sys.executable, "-c", QUERY)
            os.write(write_fd, b"done\n")

        # Model tcpdump 4.99.4: its -L listing goes to stderr, while actual
        # capture still uses the installed tcpdump and the isolated interfaces.
        original_run = subprocess.run

        def server_style_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            result = original_run(args, **kwargs)
            if len(args) > 5 and args[0] == IP and args[4] == observer.TCPDUMP and "-L" in args:
                return subprocess.CompletedProcess(
                    result.args, result.returncode, b"", result.stdout + result.stderr
                )
            return result

        subprocess.run = server_style_run
        try:
            with os.fdopen(read_fd, "rb") as stop_stream:
                report = observer.observe(1, send_synthetic_query, stop_stream)
        finally:
            subprocess.run = original_run
        expected = {"outbound": 1, "inbound": 1}
        if not report["complete"] or report["counts"] != {"tunnel": expected, "uplink": expected}:
            print("synthetic_counts", report["counts"], flush=True)
            raise RuntimeError("synthetic_capture_incorrect")
        server.wait(timeout=5)
        negative_read, negative_write = os.pipe()
        try:
            def send_without_responder() -> None:
                result = subprocess.run(  # noqa: S603 - isolated synthetic negative probe
                    [IP, "netns", "exec", "dnsclient", sys.executable, "-c", QUERY],
                    capture_output=True, timeout=5, check=False,
                )
                if result.returncode == 0:
                    raise RuntimeError("synthetic_dns_unexpected_response")
                os.write(negative_write, b"done\n")

            with os.fdopen(negative_read, "rb") as stop_stream:
                negative = observer.observe(1, send_without_responder, stop_stream)
            expected_missing = {"outbound": 1, "inbound": 0}
            if negative["counts"] != {"tunnel": expected_missing, "uplink": expected_missing}:
                print("synthetic_negative_counts", negative["counts"], flush=True)
                raise RuntimeError("synthetic_missing_response_incorrect")
        finally:
            os.close(negative_write)
        # TEST ONLY: drop the fixed synthetic query between tunnel and uplink.
        drop_rule = ["-i", "dv3s1", "-o", "outer0", "-p", "udp", "--dport", "53", "-j", "DROP"]
        run(IP, "netns", "exec", "dv3slot1", "/usr/sbin/iptables", "-I", "FORWARD", "1", *drop_rule)
        drop_read, drop_write = os.pipe()
        try:
            def send_dropped_query() -> None:
                result = subprocess.run(  # noqa: S603 - isolated synthetic negative probe
                    [IP, "netns", "exec", "dnsclient", sys.executable, "-c", QUERY],
                    capture_output=True, timeout=5, check=False,
                )
                if result.returncode == 0:
                    raise RuntimeError("dropped_dns_unexpected_response")
                os.write(drop_write, b"done\n")

            with os.fdopen(drop_read, "rb") as stop_stream:
                dropped = observer.observe(1, send_dropped_query, stop_stream)
            expected_drop = {
                "tunnel": {"outbound": 1, "inbound": 0},
                "uplink": {"outbound": 0, "inbound": 0},
            }
            if dropped["counts"] != expected_drop:
                print("synthetic_drop_counts", dropped["counts"], flush=True)
                raise RuntimeError("synthetic_drop_localization_incorrect")
        finally:
            os.close(drop_write)
            run(IP, "netns", "exec", "dv3slot1", "/usr/sbin/iptables", "-D", "FORWARD", *drop_rule)
        # TEST ONLY: allow the upstream response but drop it inside the slot.
        reply_rule = ["-i", "outer0", "-o", "dv3s1", "-p", "udp", "--sport", "53", "-j", "DROP"]
        run(
            IP, "netns", "exec", "dv3slot1", "/usr/sbin/iptables", "-I", "FORWARD", "1",
            *reply_rule,
        )
        reply_server = subprocess.Popen(  # noqa: S603 - isolated synthetic endpoint
            [IP, "netns", "exec", "dnswan", sys.executable, "-c", RESPONDER],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        reply_read, reply_write = os.pipe()
        try:
            time.sleep(0.3)

            def send_without_forwarded_reply() -> None:
                result = subprocess.run(  # noqa: S603 - isolated synthetic negative probe
                    [IP, "netns", "exec", "dnsclient", sys.executable, "-c", QUERY],
                    capture_output=True, timeout=5, check=False,
                )
                if result.returncode == 0:
                    raise RuntimeError("dropped_reply_unexpectedly_received")
                os.write(reply_write, b"done\n")

            with os.fdopen(reply_read, "rb") as stop_stream:
                blocked_reply = observer.observe(1, send_without_forwarded_reply, stop_stream)
            expected_reply_drop = {
                "tunnel": {"outbound": 1, "inbound": 0},
                "uplink": {"outbound": 1, "inbound": 1},
            }
            if blocked_reply["counts"] != expected_reply_drop:
                print("synthetic_reply_drop_counts", blocked_reply["counts"], flush=True)
                raise RuntimeError("synthetic_reply_drop_localization_incorrect")
        finally:
            os.close(reply_write)
            if reply_server.poll() is None:
                reply_server.terminate()
            reply_server.wait(timeout=5)
            run(IP, "netns", "exec", "dv3slot1", "/usr/sbin/iptables", "-D", "FORWARD", *reply_rule)
        timeout_read, timeout_write = os.pipe()
        try:
            observer.CAPTURE_SECONDS = 1
            with os.fdopen(timeout_read, "rb") as stop_stream:
                try:
                    observer.observe(1, lambda: None, stop_stream)
                except observer.ObservationError as exc:
                    if str(exc) != "capture_sync_timeout":
                        raise
                else:
                    raise RuntimeError("missing_completion_marker_was_accepted")
        finally:
            os.close(timeout_write)
        print("synthetic_dns_observer_lab_passed")
    finally:
        os.close(write_fd)
        if server.poll() is None:
            server.terminate()
        server.wait(timeout=5)


if __name__ == "__main__":
    main()

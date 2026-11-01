"""I-028 TEST ONLY: fixed, short DNS header counts in one staging namespace.

Never persist packet data, DNS names, IPs, ports, process output or pcap files.
This module is not a general capture service and accepts no capture parameters.
"""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import time
from typing import Any

IP = "/usr/sbin/ip"
TCPDUMP = "/usr/bin/tcpdump"
TIMEOUT = "/usr/bin/timeout"
CAPTURE_SECONDS = 12
PACKET_LIMIT = 64
MAX_TEXT_BYTES = 32768
TEST_PORT = 42428  # Bound exclusively by the test instrumentation for this observation.
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


class ObservationError(Exception):
    """Fixed failure category; never expose captured packet output."""


def parse_header(line: str, local_address: str) -> str:
    """Return direction only. Fixed network addresses are never returned."""
    pattern = re.compile(
        r"^\d+(?:\.\d+)? IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > "
        r"(\d+\.\d+\.\d+\.\d+)\.(\d+): .*$"
    )
    match = pattern.fullmatch(line.strip())
    if match is None:
        raise ObservationError("packet_header_invalid")
    source, source_port, destination, destination_port = match.groups()
    if (
        source == local_address and source_port == str(TEST_PORT)
        and destination == "1.1.1.1" and destination_port == "53"
    ):
        return "outbound"
    if (
        destination == local_address and destination_port == str(TEST_PORT)
        and source == "1.1.1.1" and source_port == "53"
    ):
        return "inbound"
    raise ObservationError("packet_filter_mismatch")


def capture_commands(slot: int, linktypes: dict[str, str]) -> dict[str, tuple[list[str], str]]:
    if type(slot) is not int or slot not in (1, 2):
        raise ObservationError("slot_invalid")
    outer_address = f"10.203.80.{4 * (slot - 1) + 2}"
    result = {}
    for name, interface, local_address in (
        ("tunnel", f"dv3s{slot}", "10.203.71.2"),
        ("uplink", "outer0", outer_address),
    ):
        linktype = linktypes.get(name)
        if linktype not in {"EN10MB", "RAW"}:
            raise ObservationError("capture_linktype_unsupported")
        expression = (
            "ip and udp and (("
            f"src host {local_address} and src port {TEST_PORT} and "
            "dst host 1.1.1.1 and dst port 53) or ("
            "src host 1.1.1.1 and src port 53 and "
            f"dst host {local_address} and dst port {TEST_PORT}))"
        )
        result[name] = (
            [
                TIMEOUT,
                "--signal=INT",
                "--kill-after=2s",
                "20s",
                IP,
                "netns",
                "exec",
                f"dv3slot{slot}",
                TCPDUMP,
                "-nn",
                "-q",
                "-tt",
                "-l",
                "--immediate-mode",
                "-y",
                linktype,
                "-s",
                "42" if linktype == "EN10MB" else "32",
                "-c",
                str(PACKET_LIMIT),
                "-i",
                interface,
                expression,
            ],
            local_address,
        )
    return result


def discover_linktypes(slot: int) -> dict[str, str]:
    if type(slot) is not int or slot not in (1, 2):
        raise ObservationError("slot_invalid")
    result = {}
    for name, interface in (("tunnel", f"dv3s{slot}"), ("uplink", "outer0")):
        process = subprocess.run(  # noqa: S603 - fixed executables and reviewed interface names
            [IP, "netns", "exec", f"dv3slot{slot}", TCPDUMP, "-L", "-i", interface],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=3, check=False, env=ENV,
        )
        if process.returncode != 0 or len(process.stdout) + len(process.stderr) > 4096:
            raise ObservationError("capture_linktype_unavailable")
        # tcpdump versions differ: -L lists link types on stdout or stderr.
        # Neither stream is logged or returned; only supported names are retained.
        lines = (process.stdout + b"\n" + process.stderr).decode(
            "ascii", errors="strict"
        ).splitlines()
        available = [m.group(1) for line in lines if (m := re.match(r"^\s+(EN10MB|RAW)\b", line))]
        if not available:
            raise ObservationError("capture_linktype_unsupported")
        result[name] = available[0]
    return result


def observe(slot: int, on_ready: Any, stop_stream: Any = None) -> dict[str, Any]:
    """Count only fixed test headers. Fail closed on loss, limits, or incomplete startup."""
    try:
        commands = capture_commands(slot, discover_linktypes(slot))
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError) as exc:
        raise ObservationError("capture_unavailable") from exc
    processes: dict[str, subprocess.Popen[bytes]] = {}
    selector = selectors.DefaultSelector()
    buffers = {name: {"stdout": "", "stderr": ""} for name in commands}
    counts = {name: {"outbound": 0, "inbound": 0} for name in commands}
    ready: set[str] = set()
    dropped: dict[str, int] = {}
    total_bytes = 0
    try:
        for name, (command, _) in commands.items():
            proc = subprocess.Popen(  # noqa: S603 - fixed paths, namespace and BPF; no shell
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=ENV,
            )
            processes[name] = proc
            if proc.stdout is None or proc.stderr is None:
                raise ObservationError("capture_stream_unavailable")
            selector.register(proc.stdout, selectors.EVENT_READ, (name, "stdout"))
            selector.register(proc.stderr, selectors.EVENT_READ, (name, "stderr"))
        if stop_stream is not None:
            selector.register(stop_stream, selectors.EVENT_READ, ("control", "stdin"))
        startup_deadline = time.monotonic() + 5
        end: float | None = None
        stop_after: float | None = None
        while True:
            now = time.monotonic()
            if end is None and now >= startup_deadline:
                raise ObservationError("capture_not_ready")
            if end is not None and now >= end:
                if stop_stream is not None:
                    raise ObservationError("capture_sync_timeout")
                break
            if stop_after is not None and now >= stop_after:
                break
            if any(p.poll() is not None for p in processes.values()):
                raise ObservationError("capture_ended_early")
            for key, _ in selector.select(timeout=0.2):
                name, stream = key.data
                if stream == "stdin":
                    if end is None or os.read(key.fd, 16) != b"done\n":
                        raise ObservationError("capture_sync_invalid")
                    stop_after = time.monotonic() + 0.5
                    selector.unregister(stop_stream)
                    continue
                data = os.read(key.fd, 4096)
                if not data:
                    raise ObservationError("capture_stream_closed")
                total_bytes += len(data)
                if total_bytes > MAX_TEXT_BYTES:
                    raise ObservationError("capture_output_limit")
                buffers[name][stream] += data.decode("ascii", errors="strict")
                while "\n" in buffers[name][stream]:
                    line, buffers[name][stream] = buffers[name][stream].split("\n", 1)
                    if stream == "stderr":
                        if "listening on " in line:
                            ready.add(name)
                    else:
                        direction = parse_header(line, commands[name][1])
                        counts[name][direction] += 1
                        if sum(counts[name].values()) >= PACKET_LIMIT:
                            raise ObservationError("capture_packet_limit")
            if end is None and len(ready) == len(commands):
                on_ready()
                end = time.monotonic() + CAPTURE_SECONDS
        for proc in processes.values():
            os.killpg(proc.pid, signal.SIGINT)  # type: ignore[attr-defined]  # POSIX-only observer.
        for name, proc in processes.items():
            tail_out, tail_err = proc.communicate(timeout=3)
            if len(tail_out) + len(tail_err) > MAX_TEXT_BYTES:
                raise ObservationError("capture_output_limit")
            text = buffers[name]["stdout"] + tail_out.decode("ascii", errors="strict")
            for line in text.splitlines():
                if not line.strip():
                    continue
                direction = parse_header(line, commands[name][1])
                counts[name][direction] += 1
            errors = buffers[name]["stderr"] + tail_err.decode("ascii", errors="strict")
            match = re.search(r"(?m)^(\d+) packets dropped by kernel$", errors)
            if proc.returncode not in (0, -signal.SIGINT) or match is None:
                raise ObservationError("capture_completion_unknown")
            dropped[name] = int(match.group(1))
            if dropped[name] != 0 or sum(counts[name].values()) >= PACKET_LIMIT:
                raise ObservationError("capture_incomplete")
        return {"complete": True, "counts": counts, "packet_loss": False}
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError) as exc:
        raise ObservationError("capture_unavailable") from exc
    finally:
        selector.close()
        for proc in processes.values():
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]  # POSIX-only.
                except ProcessLookupError:
                    pass
            try:
                proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                pass

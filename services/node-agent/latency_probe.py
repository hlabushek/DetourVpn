"""ADR-102 UDP latency echo for the client's node list. No accounts, keys or tunnel state.

Request: exactly 16 bytes, ``DVP1`` + 12 random bytes. Reply: ``DVR1`` + the same 12 bytes, so a
reply is never larger than its request (no amplification). Anything else is dropped silently.
A token bucket per source address and a global packet budget bound the work; nothing is logged
per request and no address is stored beyond the bounded rate table.

Runs as its own systemd unit with every capability dropped, separate from the node agent.
"""

import os
import signal
import socket
import time
from collections import OrderedDict
from typing import Any

REQUEST = b"DVP1"
REPLY = b"DVR1"
SIZE = 16
PORT = int(os.environ.get("DETOURVPN_PROBE_PORT", "51899"))
RATE_PER_S = 10.0
BURST = 20.0
GLOBAL_PER_S = 2000
TABLE_LIMIT = 4096
STOP = False


class Limiter:
    """Per-source token bucket with a bounded LRU table plus a global per-second cap."""

    def __init__(self, now: float) -> None:
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self.second, self.count = int(now), 0

    def allow(self, source: str, now: float) -> bool:
        if int(now) != self.second:
            self.second, self.count = int(now), 0
        if self.count >= GLOBAL_PER_S:
            return False
        tokens, at = self.buckets.pop(source, (BURST, now))
        tokens = min(BURST, tokens + (now - at) * RATE_PER_S)
        if tokens < 1:
            self.buckets[source] = (tokens, now)
            return False
        self.buckets[source] = (tokens - 1, now)
        while len(self.buckets) > TABLE_LIMIT:
            self.buckets.popitem(last=False)
        self.count += 1
        return True


def answer(packet: bytes) -> bytes | None:
    if len(packet) != SIZE or not packet.startswith(REQUEST):
        return None
    return REPLY + packet[len(REQUEST) :]


def stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def serve(sock: socket.socket) -> None:
    limiter = Limiter(time.monotonic())
    sock.settimeout(1.0)
    while not STOP:
        try:
            packet, source = sock.recvfrom(64)
        except TimeoutError:
            continue
        except OSError:
            continue
        reply = answer(packet)
        if reply is None or not limiter.allow(source[0], time.monotonic()):
            continue
        try:
            sock.sendto(reply, source)
        except OSError:
            continue


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not 1024 < PORT < 65536:
        print('{"error":"latency_probe_port_invalid"}', flush=True)
        return 1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("0.0.0.0", PORT))  # noqa: S104 - public probe endpoint by design (ADR-102)
        print('{"event":"latency_probe_ready"}', flush=True)
        serve(sock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

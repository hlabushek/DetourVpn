"""Actual Linux nftables gate acceptance in one disposable local WSL namespace."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NS = "vpn-i012-kernel"


def run(args: list[str], data: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local tools and namespace
        args, input=data, capture_output=True, text=True, timeout=20, check=False
    )
    if result.returncode:
        # This local kernel-only harness has no credentials/config/private keys.
        print(result.stderr[-1600:])
        raise RuntimeError("kernel_acceptance_command_failed")
    return result.stdout


PROBE = r"""
import socket, threading, time, json
server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server.bind(("127.0.0.1", 51892)); server.settimeout(.1)
running = True
def echo():
    while running:
        try:
            data, peer = server.recvfrom(2048)
            server.sendto(data, peer)
        except socket.timeout: pass
worker = threading.Thread(target=echo); worker.start()
client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); client.settimeout(.015)
passed = missed = 0
for _ in range(160):
    client.sendto(bytes(1024), ("127.0.0.1", 51892))
    try:
        client.recvfrom(2048); passed += 1
    except socket.timeout: missed += 1
running = False; worker.join(1); client.close(); server.close()
print(json.dumps({"passed": passed, "missed": missed}))
"""

EXPIRY = r"""
import socket, time, json
receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receiver.bind(("127.0.0.1", 51892)); receiver.settimeout(.15)
sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sender.sendto(b"first", ("127.0.0.1", 51892)); before = receiver.recv(16) == b"first"
time.sleep(2.2)
sender.sendto(b"late", ("127.0.0.1", 51892))
blocked = False
try: receiver.recv(16)
except socket.timeout: blocked = True
print(json.dumps({"before": before, "blocked": blocked}))
"""


def main() -> int:
    if "microsoft" not in platform.release().lower() or getattr(os, "geteuid", lambda: -1)() != 0:
        raise RuntimeError("local_wsl_root_only")
    module_spec = importlib.util.spec_from_file_location(
        "i012_agent", ROOT / "services/node-agent/agent.py"
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError("agent_module_missing")
    agent: Any = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(agent)
    agent.command = run
    agent.NS = NS
    namespaces = run(["/usr/sbin/ip", "netns", "list"])
    if NS in namespaces:
        raise RuntimeError("owned_namespace_already_exists")
    before = run(["/usr/sbin/ip", "-j", "route", "show", "table", "all"])
    created = False
    report = {
        "real_kernel_budget": False,
        "real_kernel_timeout_without_agent": False,
        "cleanup_complete": False,
        "host_routes_unchanged": False,
    }
    try:
        run(["/usr/sbin/ip", "netns", "add", NS])
        created = True
        run(["/usr/sbin/ip", "-n", NS, "link", "set", "lo", "up"])
        native = agent.Native("AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        native.initialize_gate()
        native.gate(65536, 60)
        budget = json.loads(run(["/usr/sbin/ip", "netns", "exec", NS, sys.executable, "-c", PROBE]))
        report["real_kernel_budget"] = 0 < budget["passed"] < 40 and budget["missed"] > 100
        native.gate(65536, 2)
        expiry = json.loads(
            run(["/usr/sbin/ip", "netns", "exec", NS, sys.executable, "-c", EXPIRY])
        )
        report["real_kernel_timeout_without_agent"] = expiry["before"] and expiry["blocked"]
    finally:
        if created:
            run(["/usr/sbin/ip", "netns", "delete", NS])
        report["cleanup_complete"] = NS not in run(["/usr/sbin/ip", "netns", "list"])
        report["host_routes_unchanged"] = before == run(
            ["/usr/sbin/ip", "-j", "route", "show", "table", "all"]
        )
        print(json.dumps(report, sort_keys=True))
    return 0 if all(report.values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print('{"error":"kernel_acceptance_failed"}')
        raise SystemExit(1) from None

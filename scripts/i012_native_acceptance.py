"""Real AWG + node daemon operations in disposable LOCAL WSL namespaces, no WAN."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from awg_local_smoke import AWG_PARAMETERS, ENV, IP, prepare_executables, require_environment, run

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    require_environment(None)
    if not Path("/proc/sys/kernel/osrelease").read_text().lower().find("microsoft") >= 0:
        raise RuntimeError("local_wsl_only")
    server_ns, client_ns = "vpn-i012-srv", "vpn-i012-cli"
    existing = run([IP, "netns", "list"])
    if server_ns in existing or client_ns in existing:
        raise RuntimeError("owned_namespace_exists")
    for interface in ("v11awg", "v12cli"):
        if Path("/run/amneziawg", interface + ".sock").exists():
            raise RuntimeError("owned_socket_exists")
    baseline = run([IP, "-j", "route", "show", "table", "all"])
    base = Path("/root/.cache/project-vpn-i012")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix="native-", dir=base))
    processes: list[subprocess.Popen[bytes]] = []
    created: list[str] = []
    report = {
        "real_add_and_handshake": False,
        "bidirectional_packets": False,
        "real_remove_blocks_traffic": False,
        "replayed_add_stays_removed": False,
        "kernel_expiry_without_agent": False,
        "kernel_budget_blocks_traffic": False,
        "cleanup_complete": False,
        "host_routes_unchanged": False,
    }
    stage = "prepare"
    try:
        binaries = prepare_executables(ROOT / ".cache/i-010/artifacts", work)
        awg, core = binaries["awg"], binaries["amneziawg-go"]
        spec = importlib.util.spec_from_file_location(
            "agent_native", ROOT / "services/node-agent/agent.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("module_missing")
        agent: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        agent.NS, agent.AWG = server_ns, Path(awg)
        stage = "namespaces"
        for ns in (server_ns, client_ns):
            run([IP, "netns", "add", ns])
            created.append(ns)
            run([IP, "-n", ns, "link", "set", "lo", "up"])
        run(
            [
                IP,
                "-n",
                server_ns,
                "link",
                "add",
                "slink",
                "type",
                "veth",
                "peer",
                "name",
                "clink",
                "netns",
                client_ns,
            ]
        )
        for ns, link, address in (
            (server_ns, "slink", "10.204.72.1/30"),
            (client_ns, "clink", "10.204.72.2/30"),
        ):
            run([IP, "-n", ns, "addr", "add", address, "dev", link])
            run([IP, "-n", ns, "link", "set", link, "up"])
        stage = "native_core"
        for ns, interface in ((server_ns, "v11awg"), (client_ns, "v12cli")):
            processes.append(
                subprocess.Popen(  # noqa: S603 - verified executable, owned namespaces
                    [IP, "netns", "exec", ns, core, "-f", interface],
                    env=ENV,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            for _ in range(60):
                if Path("/run/amneziawg", interface + ".sock").exists():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("native_socket_timeout")
        server_key, client_key, protection = [run([awg, "genkey"]).strip() for _ in range(3)]
        server_public = run([awg, "pubkey"], server_key + "\n").strip()
        client_public = run([awg, "pubkey"], client_key + "\n").strip()
        server_config = (
            f"[Interface]\nPrivateKey = {server_key}\nListenPort = 51892\n"
            + AWG_PARAMETERS
            + f"HeaderProtectionKey = {protection}\n"
        )
        client_config = (
            f"[Interface]\nPrivateKey = {client_key}\n"
            + AWG_PARAMETERS
            + f"HeaderProtectionKey = {protection}\n[Peer]\nPublicKey = {server_public}\n"
            + "AllowedIPs = 10.203.71.1/32\nEndpoint = 10.204.72.1:51892\nPersistentKeepalive = 1\n"
        )
        for interface, configuration in (("v11awg", server_config), ("v12cli", client_config)):
            run([awg, "setconf", interface, "/dev/stdin"], configuration)
        del server_key, client_key, server_config, client_config, protection
        for ns, interface, address in (
            (server_ns, "v11awg", "10.203.71.1/30"),
            (client_ns, "v12cli", "10.203.71.2/30"),
        ):
            run([IP, "-n", ns, "addr", "add", address, "dev", interface])
            run([IP, "-n", ns, "link", "set", interface, "mtu", "1280", "up"])
        namespace_fd = os.open(f"/proc/{processes[0].pid}/ns/net", os.O_RDONLY)
        native = agent.Native(server_public, namespace_fd=namespace_fd)
        daemon = agent.Agent(native, work / "journal.json")
        daemon.startup()

        def lease(seconds: int = 45) -> dict[str, Any]:
            return {
                "command_id": str(uuid4()),
                "peer_id": str(uuid4()),
                "peer_version": 1,
                "operation": "add_peer",
                "public_key": client_public,
                "lease_deadline": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
                "budget_bytes": 65536,
            }

        def ping() -> bool:
            return bool(
                run(
                    [
                        IP,
                        "netns",
                        "exec",
                        client_ns,
                        "/usr/bin/ping",
                        "-n",
                        "-c",
                        "1",
                        "-W",
                        "1",
                        "10.203.71.1",
                    ],
                    required=False,
                )
            )

        stage = "add"
        body = lease()
        ack = daemon.execute(body)

        def reset_client() -> None:
            run([awg, "set", "v12cli", "peer", server_public, "remove"])
            run(
                [
                    awg,
                    "set",
                    "v12cli",
                    "peer",
                    server_public,
                    "allowed-ips",
                    "10.203.71.1/32",
                    "endpoint",
                    "10.204.72.1:51892",
                    "persistent-keepalive",
                    "1",
                ]
            )

        reset_client()
        if not ack["applied"]:
            raise RuntimeError("not_applied")
        stage = "packets"
        for _ in range(5):
            if ping():
                break
        else:
            print(
                json.dumps(
                    {
                        "native_debug": {
                            "server_counters": native.counters(client_public),
                            "server_listen": run([awg, "show", "v11awg", "listen-port"]).strip(),
                            "client_listen": run([awg, "show", "v12cli", "listen-port"]).strip(),
                            "nft": json.loads(
                                run(
                                    [
                                        IP,
                                        "netns",
                                        "exec",
                                        server_ns,
                                        "/usr/sbin/nft",
                                        "-j",
                                        "list",
                                        "table",
                                        "inet",
                                        "vpn_i012",
                                    ]
                                )
                            ),
                        }
                    }
                )
            )
            raise RuntimeError("no_packets")
        handshake = run([awg, "show", "v11awg", "latest-handshakes"]).split()
        report["real_add_and_handshake"] = len(handshake) == 2 and int(handshake[1]) > 0
        rx, tx = native.counters(client_public)
        report["bidirectional_packets"] = rx > 0 and tx > 0
        stage = "remove"
        removed = daemon.execute(body | {"operation": "remove_peer", "peer_version": 2})
        report["real_remove_blocks_traffic"] = (
            not ping() and native.peers() == [] and removed["rx_bytes"] > 0
        )
        report["replayed_add_stays_removed"] = (
            not daemon.execute(body)["applied"] and native.peers() == []
        )
        stage = "expiry"
        short = lease(8)
        daemon.execute(short)
        reset_client()
        if not ping():
            raise RuntimeError("expiry_initial_packets_failed")
        time.sleep(8.2)
        report["kernel_expiry_without_agent"] = not ping() and native.peers() == [client_public]
        daemon.execute(short | {"operation": "remove_peer", "peer_version": 2})
        stage = "budget"
        quota = lease()
        daemon.execute(quota)
        reset_client()
        if not ping():
            raise RuntimeError("budget_initial_packets_failed")
        run(
            [
                IP,
                "netns",
                "exec",
                client_ns,
                "/usr/bin/ping",
                "-n",
                "-c",
                "120",
                "-i",
                ".01",
                "-s",
                "1000",
                "-W",
                "1",
                "10.203.71.1",
            ],
            required=False,
        )
        report["kernel_budget_blocks_traffic"] = not ping() and native.peers() == [client_public]
        daemon.execute(quota | {"operation": "remove_peer", "peer_version": 2})
    except Exception as exc:
        report["failed_at"] = stage  # type: ignore[assignment]
        report["failure_type"] = type(exc).__name__  # type: ignore[assignment]
    finally:
        if "namespace_fd" in locals():
            os.close(namespace_fd)
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for ns in reversed(created):
            run([IP, "netns", "delete", ns])
        for interface in ("v11awg", "v12cli"):
            path = Path("/run/amneziawg", interface + ".sock")
            if path.exists():
                path.unlink()
        report["cleanup_complete"] = all(
            ns not in run([IP, "netns", "list"]) for ns in (server_ns, client_ns)
        )
        report["host_routes_unchanged"] = baseline == run(
            [IP, "-j", "route", "show", "table", "all"]
        )
        target = work.resolve()
        if target.parent == base.resolve() and target.name.startswith("native-"):
            shutil.rmtree(target)
        print(json.dumps(report, sort_keys=True))
    return 0 if all(value is True for value in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

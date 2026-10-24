"""Local WSL AWG lab: two isolated slots, real tunnels, no internet/Android.

Requires outer `unshare --mount --net`. /run is replaced only in that private
mount namespace. Ephemeral key files stay on private tmpfs and are never printed.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

from awg_local_smoke import AWG_PARAMETERS, ENV, HASHES, IP, SmokeError, require_environment, run

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "services/node-agent" / f"{name}.py")
    if spec is None or spec.loader is None:
        raise SmokeError("adapter_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ping(namespace: str, *, allowed: bool) -> None:
    result = subprocess.run(  # noqa: S603 - fixed isolated namespace/destination
        [
            IP,
            "netns",
            "exec",
            namespace,
            "/usr/bin/ping",
            "-n",
            "-c",
            "2",
            "-i",
            "0.1",
            "-W",
            "1",
            "10.203.71.1",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        env=ENV,
        check=False,
    )
    if result.returncode != (0 if allowed else 1):
        raise SmokeError("lab_transfer_expectation_failed")


def establish(namespace: str) -> None:
    # The earlier closed-gate negative intentionally dropped the first handshake.
    # Allow the native engine's bounded retransmission interval before steady-state checks.
    result = subprocess.run(  # noqa: S603 - fixed isolated namespace/destination
        [
            IP,
            "netns",
            "exec",
            namespace,
            "/usr/bin/ping",
            "-n",
            "-c",
            "12",
            "-i",
            "0.5",
            "-W",
            "1",
            "10.203.71.1",
        ],
        capture_output=True,
        timeout=10,
        env=ENV,
        check=False,
    )
    if result.returncode:
        raise SmokeError("lab_handshake_or_transfer_failed")


def main() -> int:
    report: dict[str, Any] = {
        "scope": "local isolated WSL, pinned AWG, synthetic IPv4 packets; no WAN/Android",
        "passed": False,
    }
    processes: list[subprocess.Popen[bytes]] = []
    gates: list[Any] = []
    peers: list[Any] = []
    descriptors: list[int] = []
    bin_work: tempfile.TemporaryDirectory[str] | None = None
    stage = "isolated_environment"
    try:
        if sys.argv[1:] not in ([], ["--runtime"]):
            raise SmokeError("unsupported_lab_arguments")
        require_environment(None)
        if any(
            Path(f"/proc/self/ns/{name}").stat().st_ino == Path(f"/proc/1/ns/{name}").stat().st_ino
            for name in ("mnt", "net")
        ):
            raise SmokeError("outer_mount_and_network_namespaces_required")
        run(["/usr/bin/mount", "--make-rprivate", "/"])
        run(["/usr/bin/mount", "-t", "tmpfs", "-o", "mode=0700,nosuid,nodev", "tmpfs", "/run"])
        work = Path("/run/detour-v3-lab")
        work.mkdir(mode=0o700)
        bin_work = tempfile.TemporaryDirectory(prefix="detour-v3-bin-", dir="/root")
        binaries = {}
        for name, expected in HASHES.items():
            destination = Path(bin_work.name) / name
            shutil.copyfile(ROOT / ".cache/i-010/artifacts" / name, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
                raise SmokeError("pinned_artifact_mismatch")
            destination.chmod(0o700)
            binaries[name] = str(destination)
        awg, core = binaries["awg"], binaries["amneziawg-go"]
        gate_module, peer_module = load("window_gate"), load("window_peer")
        client_keys, server_keys = [], []
        for slot in (1, 2):
            stage = "slot_namespace_and_core"
            server, client = f"dv3-server-{slot}", f"dv3-client-{slot}"
            for namespace in (server, client):
                run([IP, "netns", "add", namespace])
                run([IP, "-n", namespace, "link", "set", "lo", "up"])
            run(
                [
                    IP,
                    "-n",
                    server,
                    "link",
                    "add",
                    "outer0",
                    "type",
                    "veth",
                    "peer",
                    "name",
                    "outer1",
                ]
            )
            run([IP, "-n", server, "link", "set", "outer1", "netns", client])
            for namespace, interface, address in (
                (server, "outer0", "192.0.2.1/30"),
                (client, "outer1", "192.0.2.2/30"),
            ):
                run([IP, "-n", namespace, "address", "add", address, "dev", interface])
                run([IP, "-n", namespace, "link", "set", interface, "up"])
            core_pids = {}
            for namespace, interface in ((server, f"dv3s{slot}"), (client, f"dv3c{slot}")):
                process = subprocess.Popen(  # noqa: S603 - pinned artifact, isolated namespace
                    [IP, "netns", "exec", namespace, core, "-f", interface],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=ENV,
                )
                processes.append(process)
                core_pids[namespace] = process.pid
                for _ in range(100):
                    if Path(f"/run/amneziawg/{interface}.sock").exists():
                        break
                    if process.poll() is not None:
                        raise SmokeError("core_exited")
                    time.sleep(0.05)
                else:
                    raise SmokeError("core_uapi_timeout")
            stage = "ephemeral_slot_configuration"
            server_secret = run([awg, "genkey"]).strip()
            client_secret = run([awg, "genkey"]).strip()
            server_public = run([awg, "pubkey"], server_secret + "\n").strip()
            client_public = run([awg, "pubkey"], client_secret + "\n").strip()
            client_keys.append(client_public)
            server_keys.append(server_public)
            protection = run([awg, "genkey"]).strip()
            for namespace, interface, private, address, peer_config in (
                (server, f"dv3s{slot}", server_secret, "10.203.71.1/30", ""),
                (
                    client,
                    f"dv3c{slot}",
                    client_secret,
                    "10.203.71.2/30",
                    f"[Peer]\nPublicKey = {server_public}\nAllowedIPs = 10.203.71.1/32\n"
                    f"Endpoint = 192.0.2.1:{51900 + slot}\n",
                ),
            ):
                config = work / f"{interface}.conf"
                fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as output:
                    output.write(
                        f"[Interface]\nPrivateKey = {private}\nListenPort = {51900 + slot}\n"
                        + AWG_PARAMETERS
                        + f"HeaderProtectionKey = {protection}\n"
                        + peer_config
                    )
                run([awg, "setconf", interface, str(config)])
                config.unlink()
                run([IP, "-n", namespace, "address", "add", address, "dev", interface])
                run([IP, "-n", namespace, "link", "set", interface, "mtu", "1280", "up"])
            del server_secret, client_secret, protection, private
            namespace_fd = os.open(f"/run/netns/{server}", os.O_RDONLY)
            descriptors.append(namespace_fd)
            gate = gate_module.SlotGate(slot, namespace_fd)
            gates.append(gate)
            gate.initialize_closed()
            peer = peer_module.SlotPeer(
                slot, Path(awg), core_pids[server], namespace_fd, server_public
            )
            peers.append(peer)
            peer.install(client_public)
            peer.install(client_public)  # Same native peer, no duplicate address/key.
            ping(client, allowed=False)
            gate.install_closed(1024 * 1024)
        for gate in gates:
            gate.open(30000)
        stage = "concurrent_tunnel_packets"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(establish, f"dv3-client-{slot}") for slot in (1, 2)]
            for result in results:
                result.result()
        report["two_real_awg_tunnels_transfer"] = True
        first_key = peers[0].inventory()
        gates[0].close()
        first_bytes = gates[0].closed_bytes()
        if not 0 < first_bytes <= 1024 * 1024:
            raise SmokeError("slot_meter_invalid")
        ping("dv3-client-1", allowed=False)
        ping("dv3-client-2", allowed=True)
        report["one_closed_slot_preserves_other"] = True
        stage = "stable_key_window_rotation"
        gates[0].install_closed(1024 * 1024)
        gates[0].open(10000)
        ping("dv3-client-1", allowed=True)
        if peers[0].inventory() != first_key:
            raise SmokeError("rotation_changed_peer")
        report["rotation_preserves_key_and_address"] = True
        gates[0].close()
        second_bytes = gates[0].closed_bytes()
        report["first_slot_window_bytes"] = [first_bytes, second_bytes]
        stage = "kernel_expiry_and_revoke"
        gates[0].install_closed(1024 * 1024)
        gates[0].open(250)
        time.sleep(0.35)
        ping("dv3-client-1", allowed=False)
        gates[0].close()
        peers[0].remove()
        gates[0].open(3000)  # TEST ONLY demonstrate removed native key cannot carry traffic.
        ping("dv3-client-1", allowed=False)
        report["kernel_expiry_and_native_revoke_block_packets"] = True
        if sys.argv[1:] == ["--runtime"]:
            from check_v3_awg_runtime import exercise

            stage = "durable_runtime_and_real_process_crashes"
            report["runtime_facts"] = exercise(
                gates, peers, client_keys, server_keys, awg, Path(bin_work.name), establish, ping
            )
        report["passed"] = True
    except Exception as error:
        report["failed_stage"] = stage
        if isinstance(error, SmokeError):
            report["failure_code"] = str(error)
        diagnostics = []
        for slot, gate in enumerate(gates, start=1):
            try:
                handshakes = run([binaries["awg"], "show", f"dv3s{slot}", "latest-handshakes"])
                diagnostics.append(
                    {
                        "slot": slot,
                        "gate_live": gate.is_live(),
                        "handshake_observed": any(
                            int(line.split()[-1]) > 0 for line in handshakes.splitlines()
                        ),
                    }
                )
            except Exception:
                diagnostics.append({"slot": slot, "diagnostics_unavailable": True})
        report["diagnostics"] = diagnostics
    finally:
        cleanup = True
        for gate in gates:
            try:
                gate.close()
                gate.dispose()
            except Exception:
                cleanup = False
        for peer in peers:
            try:
                peer.dispose()
            except Exception:
                cleanup = False
        for descriptor in descriptors:
            os.close(descriptor)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
                cleanup = False
        if bin_work is not None:
            bin_work.cleanup()
        report["cores_stopped"] = cleanup and all(
            process.poll() is not None for process in processes
        )
        report["passed"] = report["passed"] and cleanup
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

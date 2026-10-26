"""TEST ONLY AWG packet path inside an outer disposable WSL mount/network namespace.

Synthetic endpoint and service addresses exist only inside unshare namespaces;
no packets reach a physical uplink or public network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".cache" / "i-010" / "artifacts"
HASHES = {
    "awg": "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce",
    "amneziawg-go": "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe",
}
IP = "/usr/sbin/ip"
MOUNT = "/usr/bin/mount"
SYSCTL = "/usr/sbin/sysctl"
PYTHON = "/usr/bin/python3"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LOG_LEVEL": "silent", "LC_ALL": "C"}
ROUTER = "beta-i030-router"
CLIENT = "beta-i030-client"
SERVICE = "beta-i030-service"


class LabFailure(Exception):
    """Safe TEST ONLY failure with no command output or key material."""


def run(arguments: list[str], *, required: bool = True, data: str | None = None) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed lab entrypoint, no shell
            arguments,
            input=data,
            capture_output=True,
            text=True,
            env=ENV,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LabFailure("fixed_lab_command_unavailable") from None
    if required and result.returncode:
        raise LabFailure(f"fixed_lab_command_failed_rc_{result.returncode}")
    return result.stdout.strip() if result.returncode == 0 else ""


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LabFailure("lab_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def generated_key(awg: Path) -> str:
    return run([str(awg), "genkey"])


def create_link(
    host_name: str,
    peer_name: str,
    host_namespace: str | None,
    peer_namespace: str,
) -> None:
    run([IP, "link", "add", host_name, "type", "veth", "peer", "name", peer_name])
    if host_namespace is not None:
        run([IP, "link", "set", host_name, "netns", host_namespace])
    run([IP, "link", "set", peer_name, "netns", peer_namespace])


def setup_packet_lab() -> tuple[str, str]:
    """Build a fake uplink, a VPN client and an HTTP target under unshare only."""
    run([IP, "netns", "add", ROUTER])
    run([IP, "netns", "add", CLIENT])
    run([IP, "netns", "add", SERVICE])
    create_link("beta-i030-host", "dvru0", None, ROUTER)
    create_link("dvrc0", "dvc0", ROUTER, CLIENT)
    create_link("dvrs0", "dvs0", ROUTER, SERVICE)
    for namespace in (ROUTER, CLIENT, SERVICE):
        run([IP, "-n", namespace, "link", "set", "lo", "up"])
    for name, address in (("beta-i030-host", "198.18.0.1/30"), ("beta-i030-host", "8.8.8.8/32")):
        run([IP, "address", "add", address, "dev", name])
    run([IP, "link", "set", "beta-i030-host", "up"])
    for namespace, device, address in (
        (ROUTER, "dvru0", "198.18.0.2/30"),
        (ROUTER, "dvrc0", "198.18.1.1/30"),
        (ROUTER, "dvrs0", "198.18.2.1/30"),
        (CLIENT, "dvc0", "198.18.1.2/30"),
        (SERVICE, "dvs0", "198.18.2.2/30"),
    ):
        run([IP, "-n", namespace, "address", "add", address, "dev", device])
        run([IP, "-n", namespace, "link", "set", device, "up"])
    run([IP, "-n", SERVICE, "address", "add", "1.1.1.1/32", "dev", "lo"])
    run(
        [
            IP,
            "route",
            "add",
            "default",
            "via",
            "198.18.0.2",
            "dev",
            "beta-i030-host",
            "src",
            "8.8.8.8",
        ]
    )
    run([IP, "-n", ROUTER, "route", "add", "default", "via", "198.18.0.1"])
    run([IP, "-n", ROUTER, "route", "add", "1.1.1.1/32", "via", "198.18.2.2", "dev", "dvrs0"])
    # Without host masquerade, replies to the private slot pool must be dropped.
    run([IP, "-n", ROUTER, "route", "add", "blackhole", "10.203.82.0/26"])
    run([IP, "-n", CLIENT, "route", "add", "default", "via", "198.18.1.1", "dev", "dvc0"])
    run([IP, "-n", CLIENT, "route", "add", "8.8.8.8/32", "via", "198.18.1.1", "dev", "dvc0"])
    run([IP, "-n", SERVICE, "route", "add", "default", "via", "198.18.2.1", "dev", "dvs0"])
    run([IP, "netns", "exec", ROUTER, SYSCTL, "-q", "-w", "net.ipv4.ip_forward=1"])
    run([SYSCTL, "-q", "-w", "net.ipv4.ip_forward=1"])
    return run([IP, "-j", "-4", "route", "show"]), run(["/usr/sbin/nft", "-j", "list", "ruleset"])


def main() -> int:
    result: dict[str, Any] = {
        "scope": (
            "TEST ONLY real AWG/core/nft in disposable WSL netns with simulated uplink; "
            "no physical egress"
        ),
        "passed": False,
    }
    processes: dict[int, subprocess.Popen[bytes]] = {}
    packet_processes: list[subprocess.Popen[bytes]] = []
    core_pids: dict[int, int] = {}
    bootstrap: Any = None
    active_namespace_ids: list[int] = []
    temporary: tempfile.TemporaryDirectory[str] | None = None
    outer_routes_before = ""
    outer_firewall_before = ""
    packet_routes_before = ""
    packet_firewall_before = ""
    outer_forwarding_before = "0"
    passed_bytes = 0
    handshake_passed = False
    stage = "preflight"
    try:
        if getattr(os, "geteuid", lambda: -1)() != 0:
            raise LabFailure("linux_root_required")
        if any(
            Path(f"/proc/self/ns/{kind}").stat().st_ino == Path(f"/proc/1/ns/{kind}").stat().st_ino
            for kind in ("mnt", "net")
        ):
            raise LabFailure("outer_mount_and_network_namespaces_required")
        outer_routes_before = run([IP, "-j", "-4", "route", "show"])
        outer_firewall_before = run(["/usr/sbin/nft", "-j", "list", "ruleset"])
        run([MOUNT, "--make-rprivate", "/"])
        run([MOUNT, "-t", "tmpfs", "-o", "mode=0700,nosuid,nodev", "tmpfs", "/run"])
        Path("/run/netns").mkdir(mode=0o755)
        outer_forwarding_before = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
        packet_routes_before, packet_firewall_before = setup_packet_lab()
        temporary = tempfile.TemporaryDirectory(prefix="beta-slot-bootstrap-", dir="/run")
        work = Path(temporary.name)
        work.chmod(0o700)
        for name, expected in HASHES.items():
            source = ARTIFACTS / name
            destination = work / name
            shutil.copyfile(source, destination)
            destination.chmod(0o700)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
                raise LabFailure("pinned_artifact_mismatch")

        sys.path.insert(0, str(ROOT / "services" / "node-agent"))
        sys.path.insert(0, str(ROOT / "services" / "api" / "src"))
        bootstrap = load_module(
            "beta_slot_bootstrap_lab",
            ROOT / "services" / "node-agent" / "beta_slot_bootstrap.py",
        )
        preflight = load_module(
            "beta_node_preflight_lab",
            ROOT / "services" / "node-agent" / "beta_node_preflight.py",
        )
        awg = work / "awg"
        core = work / "amneziawg-go"
        private_key = generated_key(awg)
        header_key = generated_key(awg)
        try:
            key_result = subprocess.run(  # noqa: S603 - pinned AWG artifact, fixed verb
                [str(awg), "pubkey"],
                input=private_key + "\n",
                text=True,
                capture_output=True,
                env=ENV,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise LabFailure("pinned_awg_key_derivation_failed") from None
        if key_result.returncode:
            raise LabFailure("pinned_awg_key_derivation_failed")
        public_key = key_result.stdout.strip()
        slot = bootstrap.Slot.model_validate(
            {
                "slot_id": 1,
                "endpoint": "8.8.8.8:51901",
                "server_private_key": private_key,
                "server_public_key": public_key,
                "header_protection_key": header_key,
            }
        )

        run_dir = Path("/run/detourvpn-node")
        run_dir.mkdir(mode=0o700)
        config_dir = run_dir / "config"
        config_dir.mkdir(mode=0o700)
        awg_dir = config_dir / "awg"
        awg_dir.mkdir(mode=0o700)
        private(
            config_dir / "slots.json",
            json.dumps(
                {
                    "version": 1,
                    "slots": [
                        {
                            "slot_id": slot.slot_id,
                            "endpoint": slot.endpoint,
                            "server_private_key": private_key,
                            "server_public_key": public_key,
                            "header_protection_key": header_key,
                        }
                    ],
                }
            ).encode(),
        )
        private(awg_dir / "slot-1.conf", bootstrap.expected_awg_config(slot))
        private(
            config_dir / "agent.json",
            json.dumps({"slot_count": 1}).encode(),
        )
        bootstrap.CONFIG = config_dir / "slots.json"
        bootstrap.AWG_CONFIGS = awg_dir
        bootstrap.READY = run_dir / "slots"
        bootstrap.NETNS_DIR = Path("/run/netns")
        bootstrap.UAPI_DIR = Path("/run/amneziawg")
        bootstrap.AWG = awg
        bootstrap.CORE = core
        bootstrap._service_active = lambda _unit: False
        slots = bootstrap._load_slots()
        if len(slots) != 1:
            raise LabFailure("slot_count_conflict")

        real_run = bootstrap._run

        def find_core_pid(slot_id: int) -> int:
            namespace_inode = (bootstrap.NETNS_DIR / bootstrap._namespace(slot_id)).stat().st_ino
            for process_path in Path("/proc").iterdir():
                if not process_path.name.isdecimal():
                    continue
                try:
                    if (
                        process_path.stat().st_uid == 0
                        and (process_path / "ns/net").stat().st_ino == namespace_inode
                        and hashlib.sha256((process_path / "exe").read_bytes()).hexdigest()
                        == HASHES["amneziawg-go"]
                    ):
                        return int(process_path.name)
                except OSError:
                    continue
            return 0

        def systemd_test_double(
            arguments: list[str], *, data: str | None = None, required: bool = True
        ) -> str:
            if arguments[0] != bootstrap.SYSTEMCTL:
                return str(real_run(arguments, data=data, required=required))
            action = arguments[1]
            unit = arguments[-1]
            slot_id = int(unit.split("@")[1].split(".")[0])
            if action == "start":
                namespace = bootstrap._namespace(slot_id)
                processes[slot_id] = subprocess.Popen(  # noqa: S603 - pinned lab core and fixed slot
                    [IP, "netns", "exec", namespace, str(core), "-f", f"dv3s{slot_id}"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=ENV,
                )
                active_namespace_ids.append(slot_id)
                return ""
            if action == "show":
                pid = find_core_pid(slot_id)
                if pid:
                    core_pids[slot_id] = pid
                return str(pid)
            if action == "stop":
                pid = core_pids.pop(slot_id, 0) or find_core_pid(slot_id)
                if pid:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                process = processes.get(slot_id)
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                return ""
            raise LabFailure("systemd_test_double_operation_invalid")

        # Bind only the test controller; all ip/nft/AWG operations stay real.
        bootstrap._run = systemd_test_double
        stage = "actual_namespace_bootstrap"
        if bootstrap.start_slots(slots) != 1:
            raise LabFailure("slot_count_conflict")
        gate = bootstrap._gate(1)
        try:
            if gate.is_live():
                raise LabFailure("slot_gate_not_closed")
        finally:
            gate.dispose()
        preflight.check_readiness(
            config_dir / "agent.json", config_dir / "slots.json", bootstrap.READY
        )
        namespace = bootstrap._namespace(1)
        links = json.loads(run([IP, "-n", namespace, "-j", "link", "show"]))
        if {item["ifname"] for item in links} != {"lo", "dv3s1", "outer0"}:
            raise LabFailure("unexpected_slot_namespace_links")
        if "default via 10.203.82.1 dev outer0" not in run(
            [IP, "-n", namespace, "route", "show", "default"]
        ):
            raise LabFailure("slot_default_route_invalid")
        slot_rules = run(
            [
                IP,
                "netns",
                "exec",
                namespace,
                "/usr/sbin/nft",
                "list",
                "table",
                "inet",
                "detourvpn_beta_slot",
            ]
        )
        if "10.0.0.0/8" not in slot_rules or "192.168.0.0/16" not in slot_rules:
            raise LabFailure("slot_reserved_destination_rules_missing")
        if (
            run([IP, "netns", "exec", namespace, SYSCTL, "-n", "net.ipv6.conf.all.disable_ipv6"])
            != "1"
        ):
            raise LabFailure("slot_ipv6_not_disabled")

        client_private = generated_key(awg)
        client_public = run([str(awg), "pubkey"], data=client_private + "\n")
        run(
            [
                IP,
                "netns",
                "exec",
                namespace,
                str(awg),
                "set",
                "dv3s1",
                "peer",
                client_public,
                "allowed-ips",
                "10.203.71.2/32",
            ]
        )
        client_config = work / "client.conf"
        private(
            client_config,
            (
                "[Interface]\n"
                f"PrivateKey = {client_private}\n"
                + bootstrap.AWG_PARAMETERS
                + f"HeaderProtectionKey = {header_key}\n\n"
                "[Peer]\n"
                f"PublicKey = {public_key}\n"
                "AllowedIPs = 0.0.0.0/0\n"
                "Endpoint = 8.8.8.8:51901\n"
                "PersistentKeepalive = 2\n"
            ).encode(),
        )
        packet_processes.append(
            subprocess.Popen(  # noqa: S603 - pinned lab binary and fixed test namespace
                [IP, "netns", "exec", CLIENT, str(core), "-f", "awgclient0"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENV,
            )
        )
        client_uapi = Path("/run/amneziawg/awgclient0.sock")
        deadline = time.monotonic() + 5
        while not client_uapi.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not client_uapi.exists():
            raise LabFailure("test_client_core_start_failed")
        run([IP, "netns", "exec", CLIENT, str(awg), "setconf", "awgclient0", str(client_config)])
        run([IP, "-n", CLIENT, "address", "add", "10.203.71.2/32", "dev", "awgclient0"])
        run([IP, "-n", CLIENT, "link", "set", "awgclient0", "up"])
        run([IP, "-n", CLIENT, "route", "del", "default"])
        run([IP, "-n", CLIENT, "route", "add", "default", "dev", "awgclient0"])

        website = work / "website"
        website.mkdir(mode=0o700)
        (website / "proof.txt").write_text("TEST_ONLY_AWG_PACKET_OK\n", encoding="ascii")
        (website / "private.txt").write_text(
            "TEST_ONLY_PRIVATE_DESTINATION_REACHED\n", encoding="ascii"
        )
        request_code = (
            "import urllib.request; "
            "print(urllib.request.urlopen("
            "'http://1.1.1.1:8080/proof.txt', timeout=10).read().decode().strip())"
        )
        packet_processes.append(
            subprocess.Popen(  # noqa: S603 - fixed Python server in isolated test namespace
                [
                    IP,
                    "netns",
                    "exec",
                    SERVICE,
                    PYTHON,
                    "-m",
                    "http.server",
                    "8080",
                    "--bind",
                    "1.1.1.1",
                    "--directory",
                    str(website),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENV,
            )
        )
        packet_processes.append(
            subprocess.Popen(  # noqa: S603 - fixed private-address sink in isolated outer namespace
                [
                    PYTHON,
                    "-m",
                    "http.server",
                    "8081",
                    "--bind",
                    "10.203.82.1",
                    "--directory",
                    str(website),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENV,
            )
        )
        time.sleep(0.2)
        packet_gate = bootstrap._gate(1)
        stage = "actual_packet_path"
        try:
            packet_gate.open(20000)
            handshake_text = ""
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                handshake_text = run(
                    [
                        IP,
                        "netns",
                        "exec",
                        CLIENT,
                        str(awg),
                        "show",
                        "awgclient0",
                        "latest-handshakes",
                    ]
                )
                if any(
                    int(row.split("\t")[-1]) > 0
                    for row in handshake_text.splitlines()
                    if "\t" in row
                ):
                    handshake_passed = True
                    break
                time.sleep(0.1)
            else:
                raise LabFailure("test_handshake_missing")
            stage = "actual_http_egress"
            body = run(
                [
                    IP,
                    "netns",
                    "exec",
                    CLIENT,
                    PYTHON,
                    "-c",
                    request_code,
                ]
            )
            if body != "TEST_ONLY_AWG_PACKET_OK":
                raise LabFailure("test_packet_response_invalid")
            private_body = run(
                [
                    IP,
                    "netns",
                    "exec",
                    CLIENT,
                    PYTHON,
                    "-c",
                    (
                        "import urllib.request\n"
                        "try:\n"
                        " response = urllib.request.urlopen("
                        "'http://10.203.82.1:8081/private.txt', timeout=3)\n"
                        " print(response.read().decode().strip())\n"
                        "except Exception:\n"
                        " print('TEST_ONLY_BLOCKED')\n"
                    ),
                ]
            )
            if private_body != "TEST_ONLY_BLOCKED":
                raise LabFailure("private_destination_reachable")
        finally:
            try:
                packet_gate.close()
                passed_bytes = packet_gate.closed_bytes()
            finally:
                packet_gate.dispose()
        if passed_bytes <= 0:
            raise LabFailure("test_packet_gate_counter_empty")
        for process in packet_processes:
            process.terminate()
            process.wait(timeout=5)
        packet_processes.clear()
        if bootstrap.stop_slots(slots) != 1:
            raise LabFailure("slot_stop_count_conflict")
        if run([IP, "-j", "-4", "route", "show"]) != packet_routes_before:
            raise LabFailure("outer_test_routes_not_restored")
        if run(["/usr/sbin/nft", "-j", "list", "ruleset"]) != packet_firewall_before:
            raise LabFailure("outer_test_firewall_not_restored")
        if run([IP, "-j", "link", "show", "dev", "dvbh1"], required=False):
            raise LabFailure("host_veth_not_removed")
        if Path(f"/run/netns/{namespace}").exists() or bootstrap.READY.exists():
            raise LabFailure("slot_teardown_incomplete")
        if any(process.poll() is None for process in processes.values()):
            raise LabFailure("core_process_still_running")
        result["pinned_awg_core_running_in_separate_namespace"] = True
        result["server_interface_config_matches_stable_keys"] = True
        result["nft_gate_closed_and_preflight_passed"] = True
        result["host_dnat_and_two_stage_nat_delivered_test_http"] = True
        result["handshake_and_packet_budget_counter_verified"] = True
        result["ipv6_disabled_and_private_host_destination_blocked"] = True
        result["host_routes_firewall_and_veth_restored_after_teardown"] = True
        result["slot_namespace_has_only_scoped_veth_uplink"] = True
        result["orderly_teardown_completed"] = True
        result["passed"] = True
    except Exception as error:
        result["failed_stage"] = stage
        result["failure_class"] = type(error).__name__
        result["failure_location"] = [
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(error.__traceback__)[-4:]
        ]
        result["failure_code"] = (
            str(error)
            if isinstance(error, LabFailure)
            or (bootstrap is not None and isinstance(error, bootstrap.BootstrapError))
            else "lab_runtime_failed"
        )
        result["test_client_core_alive"] = any(
            process.poll() is None for process in packet_processes[:1]
        )
        result["handshake_observed"] = handshake_passed
        result["gate_counter_nonzero"] = passed_bytes > 0
    finally:
        for packet_process in packet_processes:
            if packet_process.poll() is None:
                packet_process.terminate()
                try:
                    packet_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    packet_process.kill()
                    packet_process.wait(timeout=5)
        for pid in tuple(core_pids.values()):
            try:
                if Path(f"/proc/{pid}").exists():
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if Path(f"/proc/{pid}").exists():
                        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except OSError:
                pass
        if bootstrap is not None:
            for slot_id in active_namespace_ids:
                core_process = processes.get(slot_id)
                if core_process is not None and core_process.poll() is None:
                    core_process.terminate()
                    try:
                        core_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        core_process.kill()
                        core_process.wait(timeout=5)
                namespace_path = Path(f"/run/netns/{bootstrap._namespace(slot_id)}")
                if namespace_path.exists():
                    run([IP, "netns", "delete", bootstrap._namespace(slot_id)], required=False)
            run(["/usr/sbin/nft", "delete", "table", "inet", "detourvpn_beta"], required=False)
            run(["/usr/sbin/nft", "delete", "table", "ip", "detourvpn_beta_nat"], required=False)
        for namespace in (SERVICE, CLIENT, ROUTER):
            run([IP, "netns", "delete", namespace], required=False)
        run([IP, "link", "delete", "beta-i030-host"], required=False)
        Path("/proc/sys/net/ipv4/ip_forward").write_text(outer_forwarding_before + "\n")
        if Path("/run/netns").exists():
            if run([IP, "-j", "-4", "route", "show"]) != outer_routes_before:
                result["passed"] = False
                result["outer_namespace_routes_restored"] = False
            else:
                result["outer_namespace_routes_restored"] = True
            if run(["/usr/sbin/nft", "-j", "list", "ruleset"]) != outer_firewall_before:
                result["passed"] = False
                result["outer_namespace_firewall_restored"] = False
            else:
                result["outer_namespace_firewall_restored"] = True
        if temporary is not None:
            temporary.cleanup()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

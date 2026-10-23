"""I-010 bounded namespace acceptance, never a production tunnel provisioner.

Real pinned AWG binaries, two temporary network namespaces, no internet/NAT.
Runtime test keys live only in memory and private tmpfs files. No key output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

HASHES = {
    "amneziawg-go": "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe",
    "awg": "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce",
}
IP = "/usr/sbin/ip"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LOG_LEVEL": "silent"}
# Fixed laboratory-only values. Not exported as production configuration.
AWG_PARAMETERS = (
    "Jc = 4\nJmin = 64\nJmax = 96\n"
    "S1 = 16\nS2 = 24\nS3 = 32\nS4 = 40\n"
    "H1 = 101234\nH2 = 202345\nH3 = 303456\nH4 = 404567\n"
)


class SmokeError(Exception):
    """No external command output or secret material in exceptions."""


def run(argv: list[str], data: str | None = None, *, required: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed tools/verbs, no shell
        argv, input=data, capture_output=True, text=True, timeout=12, env=ENV, check=False
    )
    if required and result.returncode != 0:
        raise SmokeError("command_failed")
    return result.stdout if result.returncode == 0 else ""


def root_routes(family: str = "4") -> list[dict[str, Any]]:
    rows = json.loads(run([IP, "-j", f"-{family}", "route", "show", "table", "all"]))
    return [
        {key: row[key] for key in ("dst", "dev", "gateway", "table", "type") if key in row}
        for row in rows
    ]


def prepare_executables(source_directory: Path, target_directory: Path) -> dict[str, str]:
    """Copy public artifacts into a private executable directory, never key tmpfs."""
    result = {}
    for name, expected in HASHES.items():
        source = source_directory.resolve() / name
        target = target_directory / name
        shutil.copyfile(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise SmokeError("artifact_hash_mismatch")
        target.chmod(0o700)
        if not os.access(target, os.X_OK):
            raise SmokeError("executable_storage_is_noexec")
        result[name] = str(target)
    return result


def require_environment(staging_address: str | None, *, i019: bool = False) -> str:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise SmokeError("linux_root_required")
    wsl = "microsoft" in platform.release().lower()
    if staging_address is None:
        if not wsl:
            raise SmokeError("explicit_staging_address_required")
        return "local_wsl_only"
    allowed = ("45.10.166.106", "85.209.132.205") if i019 else ("45.10.166.106",)
    if staging_address not in allowed or wsl:
        raise SmokeError("staging_target_not_approved")
    release = platform.freedesktop_os_release()
    if (
        release.get("ID") != "ubuntu"
        or release.get("VERSION_ID") != "24.04"
        or platform.machine() != "x86_64"
    ):
        raise SmokeError("unsupported_staging_platform")
    addresses = json.loads(run([IP, "-j", "-4", "address", "show"]))
    if not any(
        address.get("local") == staging_address
        for interface in addresses
        for address in interface.get("addr_info", [])
    ):
        raise SmokeError("staging_address_not_assigned")
    return "approved_staging_isolated_sandbox"


def staging_snapshot() -> dict[str, str]:
    """Only hash selected operational state; no keys/configs/traffic are read."""
    containers = run(
        [
            "/usr/bin/docker",
            "container",
            "inspect",
            "--format",
            "{{.Id}} {{.State.Pid}} {{.State.Running}} {{.State.StartedAt}}",
            "amnezia-awg2",
            "amnezia-wireguard",
        ]
    )
    rows = [row.split() for row in containers.splitlines()]
    if len(rows) != 2 or any(len(row) != 4 or row[2] != "true" for row in rows):
        raise SmokeError("existing_containers_not_running")
    links = json.loads(run([IP, "-j", "link", "show"]))
    data = {
        "existing_containers": containers,
        "firewall_v4": run(["/usr/sbin/iptables", "-S"]),
        "firewall_v6": run(["/usr/sbin/ip6tables", "-S"]),
        "host_ipv6_routes": json.dumps(root_routes("6"), sort_keys=True),
        "host_links": json.dumps(sorted(link["ifname"] for link in links)),
        "preexisting_namespaces": run([IP, "netns", "list"]),
    }
    return {key: hashlib.sha256(value.encode()).hexdigest() for key, value in data.items()}


def interrupt(_signum: int, _frame: Any) -> None:
    raise SmokeError("interrupted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--inject-failure-after-setup", action="store_true")
    parser.add_argument("--inject-failure-with-key-file", action="store_true")
    parser.add_argument("--approved-staging-address")
    args = parser.parse_args()
    try:
        scope = require_environment(args.approved_staging_address)
    except (SmokeError, OSError, ValueError, subprocess.SubprocessError):
        print('{"error":"environment_not_approved"}')
        return 1
    signal.signal(signal.SIGTERM, interrupt)
    stage = "prerequisites"
    report: dict[str, Any] = {"scope": scope, "passed": False, "external_ingress_tested": False}
    namespaces: list[str] = []
    processes: list[subprocess.Popen[bytes]] = []
    sockets: list[Path] = []
    directory: tempfile.TemporaryDirectory[str] | None = None
    executable_directory: tempfile.TemporaryDirectory[str] | None = None
    socket_dir = Path("/var/run/amneziawg")
    socket_dir_existed = socket_dir.exists()
    original_routes: list[dict[str, Any]] | None = None
    original_snapshot: dict[str, str] | None = None
    token = os.urandom(4).hex()
    ns_server, ns_client = f"vpn-i010-{token}-s", f"vpn-i010-{token}-c"
    server_if, client_if = f"v10s{token}", f"v10c{token}"
    try:
        if run(["/usr/bin/findmnt", "-T", "/run", "-n", "-o", "FSTYPE"]).strip() != "tmpfs":
            raise SmokeError("tmpfs_required")
        original_routes = root_routes()
        if args.approved_staging_address is not None:
            original_snapshot = staging_snapshot()
        directory = tempfile.TemporaryDirectory(prefix="vpn-i010-local-", dir="/run")
        work = Path(directory.name)
        # Ubuntu mounts /run noexec. Keys stay there; only public, verified
        # executable copies go into the service's private /tmp filesystem.
        executable_directory = tempfile.TemporaryDirectory(prefix="vpn-i010-bin-", dir="/tmp")
        binaries = prepare_executables(args.artifacts_dir, Path(executable_directory.name))
        awg, core = binaries["awg"], binaries["amneziawg-go"]
        stage = "isolated_network_setup"
        for namespace in (ns_server, ns_client):
            run([IP, "netns", "add", namespace])
            namespaces.append(namespace)
            run([IP, "-n", namespace, "link", "set", "lo", "up"])
        # Both ends are born in the test namespace; host networking is untouched.
        run([IP, "-n", ns_server, "link", "add", "lab0", "type", "veth", "peer", "name", "lab1"])
        run([IP, "-n", ns_server, "link", "set", "lab1", "netns", ns_client])
        for namespace, interface, address in (
            (ns_server, "lab0", "192.0.2.1/30"),
            (ns_client, "lab1", "192.0.2.2/30"),
        ):
            run([IP, "-n", namespace, "address", "add", address, "dev", interface])
            run([IP, "-n", namespace, "link", "set", interface, "up"])
        if args.inject_failure_after_setup:
            raise SmokeError("injected_failure")
        stage = "start_pinned_awg"
        for namespace, interface in ((ns_server, server_if), (ns_client, client_if)):
            socket = socket_dir / f"{interface}.sock"
            if socket.exists():
                raise SmokeError("unexpected_socket_collision")
            sockets.append(socket)
            process = subprocess.Popen(  # noqa: S603 - fixed command, verified binary
                [IP, "netns", "exec", namespace, core, "-f", interface],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=ENV,
            )
            processes.append(process)
            for _ in range(100):
                if socket.exists():
                    break
                if process.poll() is not None:
                    raise SmokeError("awg_exited")
                time.sleep(0.05)
            else:
                raise SmokeError("uapi_timeout")
        stage = "ephemeral_keys_and_config"
        private_server = run([awg, "genkey"]).strip()
        private_client = run([awg, "genkey"]).strip()
        public_server = run([awg, "pubkey"], private_server + "\n").strip()
        public_client = run([awg, "pubkey"], private_client + "\n").strip()
        protection_key = run([awg, "genkey"]).strip()
        for namespace, interface, private, peer_public, peer_ip, address, endpoint in (
            (
                ns_server,
                server_if,
                private_server,
                public_client,
                "198.51.100.2",
                "198.51.100.1/30",
                "",
            ),
            (
                ns_client,
                client_if,
                private_client,
                public_server,
                "198.51.100.1",
                "198.51.100.2/30",
                "192.0.2.1:51831",
            ),
        ):
            config = work / f"{interface}.conf"
            # The private parent is 0700; create key-bearing files atomically 0600.
            descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(
                    f"[Interface]\nPrivateKey = {private}\nListenPort = 51831\n"
                    + AWG_PARAMETERS
                    + f"HeaderProtectionKey = {protection_key}\n"
                    + f"[Peer]\nPublicKey = {peer_public}\nAllowedIPs = {peer_ip}/32\n"
                    + (f"Endpoint = {endpoint}\n" if endpoint else "")
                )
            if args.inject_failure_with_key_file:
                raise SmokeError("injected_failure_with_key_file")
            run([IP, "netns", "exec", namespace, awg, "setconf", interface, str(config)])
            config.unlink()
            run([IP, "-n", namespace, "address", "add", address, "dev", interface])
            run([IP, "-n", namespace, "link", "set", interface, "mtu", "1280", "up"])
        del private_server, private_client, protection_key, private
        stage = "handshake_and_bidirectional_packets"
        for namespace, destination in ((ns_client, "198.51.100.1"), (ns_server, "198.51.100.2")):
            run(
                [
                    IP,
                    "netns",
                    "exec",
                    namespace,
                    "/usr/bin/ping",
                    "-n",
                    "-c",
                    "3",
                    "-W",
                    "2",
                    destination,
                ]
            )
        for namespace, interface in ((ns_server, server_if), (ns_client, client_if)):
            handshake = run(
                [IP, "netns", "exec", namespace, awg, "show", interface, "latest-handshakes"]
            ).split()
            transfer = run(
                [IP, "netns", "exec", namespace, awg, "show", interface, "transfer"]
            ).split()
            if len(handshake) != 2 or int(handshake[1]) <= 0:
                raise SmokeError("no_handshake")
            if len(transfer) != 3 or min(int(transfer[1]), int(transfer[2])) <= 0:
                raise SmokeError("no_bidirectional_transfer")
        report["handshake"] = True
        report["bidirectional_ping"] = True
        stage = "remove_peer"
        run(
            [IP, "netns", "exec", ns_server, awg, "set", server_if, "peer", public_client, "remove"]
        )
        if run([IP, "netns", "exec", ns_server, awg, "show", server_if, "peers"]).strip():
            raise SmokeError("peer_still_present")
        denied = subprocess.run(  # noqa: S603 - fixed isolated ping destination
            [
                IP,
                "netns",
                "exec",
                ns_client,
                "/usr/bin/ping",
                "-n",
                "-c",
                "2",
                "-W",
                "1",
                "198.51.100.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=ENV,
            timeout=6,
            check=False,
        )
        if denied.returncode != 1:
            raise SmokeError("peer_removal_not_verified")
        report["peer_removal_blocks_ping"] = True
        report["passed"] = True
    except SmokeError as exc:
        report["failed_stage"] = stage
        report["failure_code"] = str(exc)  # Only fixed codes, never command output.
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
        report["failed_stage"] = stage
    finally:
        cleanup = True
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=4)
                except (OSError, subprocess.SubprocessError):
                    cleanup = False
            except OSError:
                cleanup = False
        for namespace in reversed(namespaces):
            try:
                run([IP, "netns", "delete", namespace])
            except (SmokeError, OSError, subprocess.SubprocessError):
                cleanup = False
        for socket in sockets:
            try:
                socket.unlink(missing_ok=True)
            except OSError:
                cleanup = False
        if not socket_dir_existed and socket_dir.exists():
            try:
                socket_dir.rmdir()  # Only remove the newly created directory if empty.
            except OSError:
                cleanup = False
        if directory is not None:
            try:
                directory.cleanup()
                cleanup = cleanup and not Path(directory.name).exists()
            except OSError:
                cleanup = False
        if executable_directory is not None:
            try:
                executable_directory.cleanup()
                cleanup = cleanup and not Path(executable_directory.name).exists()
            except OSError:
                cleanup = False
        try:
            remaining = run([IP, "netns", "list"])
            cleanup = cleanup and all(namespace not in remaining for namespace in namespaces)
            report["host_ipv4_routes_unchanged"] = original_routes == root_routes()
            if original_snapshot is not None:
                current_snapshot = staging_snapshot()
                for key, value in original_snapshot.items():
                    report[f"{key}_unchanged"] = value == current_snapshot[key]
                cleanup = cleanup and original_snapshot == current_snapshot
        except (SmokeError, OSError, ValueError, subprocess.SubprocessError):
            cleanup = False
        report["cleanup_complete"] = cleanup
        if not cleanup or not report.get("host_ipv4_routes_unchanged"):
            report["passed"] = False
        print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

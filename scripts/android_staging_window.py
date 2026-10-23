"""I-011 staging-only ten-minute endpoint. Never run outside the reviewed unit.

No SSH, installation, production configuration or client private keys. Command
output is captured and errors use fixed codes. Preflight performs reads only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from awg_local_smoke import AWG_PARAMETERS, ENV, HASHES, IP, SmokeError, require_environment, run

HOST = "45.10.166.106"
NS = "vpn-i011"
VETH = "v11host"
VETH_NS = "v11ns"
TUN = "v11awg"
PORT = "51892"
STATE = Path("/run/project-vpn-i011")
INPUT = Path("/run/project-vpn-i011-input/public-key")
SOCKET = Path(f"/run/amneziawg/{TUN}.sock")
BIN = Path("/opt/project-vpn/current")
UNIT = "project-vpn-i011.service"
IPT = "/usr/sbin/iptables"
CGROUP_ROOT = Path("/sys/fs/cgroup")
CONTAINER_STATE_FORMAT = (
    '{"id":{{json .Id}},"pid":{{json .State.Pid}},'
    '"running":{{json .State.Running}},"started":{{json .State.StartedAt}},'
    '"sandbox":{{json .NetworkSettings.SandboxKey}}}'
)
CHECKPOINT = "initialization"
I019 = False
LABEL = "project-vpn-i011"
CONTAINERS: tuple[str, ...] = ("amnezia-awg2", "amnezia-wireguard")


def configure_i019(host: str) -> None:
    """Explicit, fixed staging opt-in. Default I-011 paths/host remain unchanged."""
    global HOST, NS, VETH, VETH_NS, TUN, PORT, STATE, INPUT, SOCKET, UNIT
    global I019, LABEL, CONTAINERS, BLOCKED
    if host not in {"45.10.166.106", "85.209.132.205"}:
        raise SmokeError("staging_target_not_approved")
    HOST, NS, VETH, VETH_NS, TUN = host, "vpn-i019", "v19host", "v19ns", "v19awg"
    PORT = "51893"
    STATE = Path("/run/project-vpn-i019")
    INPUT = Path("/run/project-vpn-i019-input/public-key")
    SOCKET = Path(f"/run/amneziawg/{TUN}.sock")
    UNIT = "project-vpn-i019.service"
    LABEL, I019 = "project-vpn-i019", True
    CONTAINERS = () if host == "85.209.132.205" else ("amnezia-awg2", "amnezia-wireguard")
    BLOCKED = tuple(dict.fromkeys((*BLOCKED, "45.10.166.106/32", "85.209.132.205/32")))


def no_docker_baseline() -> str:
    """B-only: verify actual absence, never substitute fake container metadata."""
    if not I019 or HOST != "85.209.132.205":
        raise SmokeError("empty_container_baseline_not_approved")
    for path in ("/usr/bin/docker", "/run/docker.sock", "/run/containerd/containerd.sock"):
        if Path(path).exists():
            raise SmokeError("unexpected_container_runtime")
    for proc in Path("/proc").iterdir():
        if proc.name.isdecimal():
            try:
                name = (proc / "comm").read_text().strip()
            except FileNotFoundError:
                continue  # Process exited during the read-only snapshot.
            if name in {"dockerd", "containerd", "podman"}:
                raise SmokeError("unexpected_container_runtime")
    return "verified_absent_docker_containerd_podman"


def i019_tables() -> list[list[str]]:
    rows = json.loads(run(["/usr/sbin/nft", "-j", "list", "tables"]))["nftables"]
    return [[row["table"]["family"], row["table"]["name"]] for row in rows if "table" in row]


def cleanup_i019_tables(original: list[list[str]]) -> None:
    """B-only: remove only our newly created, now-empty iptables-nft tables.

    Never flush an existing table or delete any table containing foreign objects.
    A pristine B may have no filter/nat table before our first iptables rule.
    """
    if not I019 or HOST != "85.209.132.205":
        raise SmokeError("empty_table_cleanup_not_approved")
    expected = {
        "filter": {
            "INPUT": ("input", "filter", 0),
            "FORWARD": ("forward", "filter", 0),
            "OUTPUT": ("output", "filter", 0),
        },
        "nat": {
            "PREROUTING": ("prerouting", "nat", -100),
            "INPUT": ("input", "nat", 100),
            "OUTPUT": ("output", "nat", -100),
            "POSTROUTING": ("postrouting", "nat", 100),
        },
    }
    current = i019_tables()
    for table, chains in expected.items():
        if ["ip", table] in original or ["ip", table] not in current:
            continue
        rows = json.loads(run(["/usr/sbin/nft", "-j", "list", "table", "ip", table]))["nftables"]
        for row in rows:
            if "metainfo" in row:
                continue
            if "table" in row:
                if set(row["table"]) - {"family", "name", "handle"}:
                    raise SmokeError("foreign_table_metadata")
                continue
            if set(row) != {"chain"}:
                raise SmokeError("foreign_firewall_object")
            chain = row["chain"]
            name = chain.get("name")
            if (
                name not in chains
                or chain.get("policy") != "accept"
                or (chain.get("hook"), chain.get("type"), chain.get("prio")) != chains[name]
                or set(chain)
                - {"family", "table", "name", "handle", "type", "hook", "prio", "policy"}
            ):
                raise SmokeError("foreign_firewall_chain")
        run(["/usr/sbin/nft", "delete", "table", "ip", table])


def checkpoint(name: str) -> None:
    """Only call with fixed internal labels, never command arguments or output."""
    global CHECKPOINT
    CHECKPOINT = name


# Conservative IPv4 special/private ranges, plus the staging host itself.
BLOCKED: tuple[str, ...] = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    HOST + "/32",
)


def trusted(path: Path, *, directory: bool = False) -> None:
    """Require root ownership, no symlinks, no group/world writes on every parent."""
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise SmokeError("untrusted_path")
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise SmokeError("unexpected_file_type")


def public_key(value: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise SmokeError("invalid_public_key") from exc
    if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
        raise SmokeError("invalid_public_key")
    return value


def firewall_rules() -> list[tuple[str, str, list[str]]]:
    """Ordered root rules. Insert in reverse order; remove only exact tagged rules."""
    rules: list[tuple[str, str, list[str]]] = [
        ("filter", "INPUT", ["-i", VETH, "-j", "DROP"]),
    ]
    for cidr in BLOCKED:
        rules.append(("filter", "FORWARD", ["-i", VETH, "-d", cidr, "-j", "DROP"]))
    rules.extend(
        [
            ("filter", "FORWARD", ["-i", VETH, "!", "-s", "10.203.72.2/32", "-j", "DROP"]),
            ("filter", "FORWARD", ["-i", VETH, "-o", "ens3", "-j", "ACCEPT"]),
            (
                "filter",
                "FORWARD",
                [
                    "-i",
                    "ens3",
                    "-o",
                    VETH,
                    "-d",
                    "10.203.72.2/32",
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "ESTABLISHED,RELATED",
                    "-j",
                    "ACCEPT",
                ],
            ),
            (
                "filter",
                "FORWARD",
                [
                    "-i",
                    "ens3",
                    "-o",
                    VETH,
                    "-d",
                    "10.203.72.2/32",
                    "-p",
                    "udp",
                    "--dport",
                    PORT,
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "DNAT",
                    "-j",
                    "ACCEPT",
                ],
            ),
            ("filter", "FORWARD", ["-i", VETH, "-j", "DROP"]),
            ("filter", "FORWARD", ["-o", VETH, "-j", "DROP"]),
            (
                "nat",
                "PREROUTING",
                [
                    "-i",
                    "ens3",
                    "-d",
                    HOST,
                    "-p",
                    "udp",
                    "--dport",
                    PORT,
                    "-j",
                    "DNAT",
                    "--to-destination",
                    "10.203.72.2:" + PORT,
                ],
            ),
            ("nat", "POSTROUTING", ["-s", "10.203.72.2/32", "-o", "ens3", "-j", "MASQUERADE"]),
        ]
    )
    return rules


def rule_command(operation: str, rule: tuple[str, str, list[str]]) -> list[str]:
    table, chain, args = rule
    return [
        IPT,
        "-w",
        "5",
        "-t",
        table,
        operation,
        chain,
        "-m",
        "comment",
        "--comment",
        LABEL,
        *args,
    ]


def normalize_routes(value: Any) -> Any:
    """Ignore route lifetime countdown only, preserve destinations/metrics/nexthops."""
    if isinstance(value, dict):
        return {key: normalize_routes(item) for key, item in value.items() if key != "expires"}
    if isinstance(value, list):
        return [normalize_routes(item) for item in value]
    return value


def snapshot(*, legacy: bool = False) -> dict[str, str]:
    probes = {
        "filter_nat": ["/usr/sbin/iptables-save"],
        "ipv6_filter": ["/usr/sbin/ip6tables-save"],
        "routes4": [IP, "-j", "-4", "route", "show", "table", "all"],
        "routes6": [IP, "-j", "-6", "route", "show", "table", "all"],
        "links": [IP, "-o", "link", "show"],
        "namespaces": [IP, "netns", "list"],
    }
    if CONTAINERS:
        probes["containers"] = [
            "/usr/bin/docker",
            "inspect",
            "--format",
            "{{.Id}} {{.State.Pid}} {{.State.Running}} {{.State.StartedAt}}",
            *CONTAINERS,
        ]
    values = {name: run(argv) for name, argv in probes.items()}
    if not CONTAINERS:
        values["containers"] = no_docker_baseline()
    rows = [row.split() for row in values["containers"].splitlines()]
    if CONTAINERS and (len(rows) != 2 or any(len(row) != 4 or row[2] != "true" for row in rows)):
        raise SmokeError("existing_containers_not_running")
    # Interface indices can change on legitimate restarts: report drift, never restore blindly.
    for key in ("filter_nat", "ipv6_filter"):
        # iptables-save adds wall-clock generation comments; these are not state.
        values[key] = "\n".join(
            line for line in values[key].splitlines() if not line.startswith("#")
        )
        if not legacy:
            # Chain policy counters are present even without iptables-save -c.
            values[key] = re.sub(r"(?m)^(:\S+ \S+) \[\d+:\d+\]$", r"\1 [0:0]", values[key])
    if not legacy:
        for key in ("routes4", "routes6"):
            rows = normalize_routes(json.loads(values[key]))
            values[key] = json.dumps(
                sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)), sort_keys=True
            )
    return {key: hashlib.sha256(value.encode()).hexdigest() for key, value in values.items()}


def owned_processes_gone() -> bool:
    """Check the service cgroup, not ptrace-protected namespaces of unrelated PIDs."""
    group = run(["/usr/bin/systemctl", "show", UNIT, "--property=ControlGroup", "--value"]).strip()
    if not group:
        state = run(
            ["/usr/bin/systemctl", "show", UNIT, "--property=ActiveState", "--value"]
        ).strip()
        return state in {"inactive", "failed"}
    if group != "/system.slice/" + UNIT:
        return False
    root = CGROUP_ROOT / group.lstrip("/")
    if not (root / "cgroup.procs").is_file():
        return False
    # No delegation: all service descendants must be gone except this cleanup.
    own = str(os.getpid())
    return all(set(path.read_text().split()) <= {own} for path in root.rglob("cgroup.procs"))


def check_routes(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        dst = row.get("dst", "default")
        if dst == "default":
            continue
        network = ipaddress.ip_network(dst, strict=False)
        if any(
            network.overlaps(ipaddress.ip_network(cidr))
            for cidr in ("10.203.71.0/30", "10.203.72.0/30")
        ):
            raise SmokeError("subnet_collision")


def container_state(name: str) -> dict[str, Any]:
    row = json.loads(run(["/usr/bin/docker", "inspect", "--format", CONTAINER_STATE_FORMAT, name]))
    if (
        not isinstance(row, dict)
        or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("id", "")))
        or type(row.get("pid")) is not int
        or row["pid"] <= 1
        or row.get("running") is not True
        or not isinstance(row.get("started"), str)
        or not row["started"]
    ):
        raise SmokeError("invalid_container_state")
    return row


def docker_namespace_path(value: Any) -> Path:
    # Docker-managed bind mounts need no access to another process's /proc.
    # No host-mode/default sandbox, caller-supplied namespace, or path traversal.
    if not isinstance(value, str) or not re.fullmatch(
        r"/(?:var/)?run/docker/netns/[a-f0-9]{12,64}", value
    ):
        raise SmokeError("invalid_docker_namespace_path")
    path = Path(value).resolve(strict=True)
    if path.parent != Path("/run/docker/netns"):
        raise SmokeError("invalid_docker_namespace_path")
    trusted(path)
    return path


def check_container_routes(name: str) -> None:
    before = container_state(name)
    path = docker_namespace_path(before.get("sandbox"))
    inode_before = path.stat()
    rows = json.loads(
        run(
            [
                "/usr/bin/nsenter",
                f"--net={path}",
                "--",
                IP,
                "-j",
                "-4",
                "route",
                "show",
                "table",
                "all",
            ]
        )
    )
    after = container_state(name)
    inode_after = path.stat()
    if before != after or (inode_before.st_dev, inode_before.st_ino) != (
        inode_after.st_dev,
        inode_after.st_ino,
    ):
        raise SmokeError("container_changed_during_probe")
    check_routes(rows)


def preflight(*, allow_disabled_forwarding: bool = False) -> dict[str, str]:
    checkpoint("environment")
    require_environment(HOST, i019=I019)
    checkpoint("trusted_paths")
    for path in (Path(__file__).absolute(), Path(__file__).with_name("awg_local_smoke.py")):
        trusted(path)
    trusted(STATE, directory=True)
    if any(STATE.iterdir()):
        raise SmokeError("state_not_empty")
    checkpoint("tmpfs")
    if run(["/usr/bin/findmnt", "-T", "/run", "-n", "-o", "FSTYPE"]).strip() != "tmpfs":
        raise SmokeError("tmpfs_required")
    if Path(f"/run/netns/{NS}").exists() or SOCKET.exists():
        raise SmokeError("namespace_or_socket_collision")
    checkpoint("host_links")
    links = json.loads(run([IP, "-j", "link", "show"]))
    if any(row["ifname"] in (VETH, VETH_NS, TUN) for row in links):
        raise SmokeError("interface_collision")
    checkpoint("host_default_route")
    defaults = json.loads(run([IP, "-j", "-4", "route", "show", "default"]))
    if len(defaults) != 1 or defaults[0].get("dev") != "ens3":
        raise SmokeError("unexpected_default_route")
    checkpoint("host_forwarding")
    forwarding = run(["/usr/sbin/sysctl", "-n", "net.ipv4.ip_forward"]).strip()
    if forwarding not in ({"0", "1"} if I019 and allow_disabled_forwarding else {"1"}):
        raise SmokeError("host_forwarding_not_enabled")
    checkpoint("udp_listener")
    if PORT in run(["/usr/bin/ss", "-H", "-lun"]):
        raise SmokeError("udp_port_collision")
    checkpoint("host_firewall")
    firewall = run(["/usr/sbin/iptables-save"])
    nftables = run(["/usr/sbin/nft", "-j", "list", "ruleset"])
    if LABEL in firewall or PORT in firewall or PORT in nftables:
        raise SmokeError("firewall_collision")
    checkpoint("host_routes")
    check_routes(json.loads(run([IP, "-j", "-4", "route", "show", "table", "all"])))
    # Include Docker IPAM and each existing container's network namespace routes.
    checkpoint("docker_ipam")
    networks = run(["/usr/bin/docker", "network", "ls", "-q"]).split() if CONTAINERS else []
    if not CONTAINERS:
        no_docker_baseline()
        if "nf_tables" not in run([IPT, "--version"]):
            raise SmokeError("i019_b_requires_iptables_nft")
    for network in networks:
        ipam = json.loads(
            run(
                [
                    "/usr/bin/docker",
                    "network",
                    "inspect",
                    "--format",
                    "{{json .IPAM.Config}}",
                    network,
                ]
            )
        )
        check_routes(
            [{"dst": item["Subnet"]} for item in (ipam or []) if ":" not in item["Subnet"]]
        )
    for container in CONTAINERS:
        checkpoint("container_routes")
        check_container_routes(container)
    for name, expected in HASHES.items():
        checkpoint("pinned_artifacts")
        path = (BIN / name).resolve()
        trusted(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SmokeError("artifact_hash_mismatch")
    checkpoint("phone_public_key")
    trusted(INPUT)
    public_key(INPUT.read_text(encoding="ascii").strip())
    checkpoint("baseline_snapshot")
    return snapshot()


def private_file(name: str, text: str) -> None:
    descriptor = os.open(STATE / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(text)


def nsrun(argv: list[str], data: str | None = None) -> str:
    return run([IP, "netns", "exec", NS, *argv], data)


def serve() -> None:
    checkpoint("systemd_guard")
    # No foreground/manual bypass of systemd's deadline and unconditional stop hook.
    if not os.environ.get("INVOCATION_ID"):
        raise SmokeError("reviewed_systemd_unit_required")
    properties = run(
        [
            "/usr/bin/systemctl",
            "show",
            UNIT,
            "-p",
            "RuntimeMaxUSec",
            "-p",
            "KillMode",
            "-p",
            "ExecStopPost",
            "-p",
            "Restart",
        ]
    )
    if (
        "RuntimeMaxUSec=10min" not in properties
        or "KillMode=control-group" not in properties
        or "Restart=no" not in properties
        or "android_staging_window.py cleanup" not in properties
    ):
        raise SmokeError("deadline_or_cleanup_missing")
    baseline = preflight()
    # Marker precedes the first mutation so ExecStopPost also handles partial setup.
    checkpoint("namespace_setup")
    metadata: dict[str, Any] = {"schema_version": 2, "hashes": baseline}
    if I019 and HOST == "85.209.132.205":
        metadata["original_nft_tables"] = i019_tables()
    private_file("baseline.json", json.dumps(metadata))
    run([IP, "netns", "add", NS])
    run([IP, "link", "add", VETH, "type", "veth", "peer", "name", VETH_NS, "netns", NS])
    run([IP, "address", "add", "10.203.72.1/30", "dev", VETH])
    run([IP, "-n", NS, "address", "add", "10.203.72.2/30", "dev", VETH_NS])
    # Install host isolation BEFORE bringing either link up; public DNAT goes last.
    checkpoint("host_firewall_setup")
    for rule in reversed(firewall_rules()):
        run(rule_command("-I", rule))
    checkpoint("namespace_firewall_setup")
    for chain in ("INPUT", "FORWARD"):
        nsrun([IPT, "-P", chain, "DROP"])
    nsrun([IPT, "-A", "INPUT", "-i", VETH_NS, "-p", "udp", "--dport", PORT, "-j", "ACCEPT"])
    nsrun([IPT, "-A", "FORWARD", "-i", TUN, "!", "-s", "10.203.71.2/32", "-j", "DROP"])
    for cidr in BLOCKED:
        nsrun([IPT, "-A", "FORWARD", "-i", TUN, "-d", cidr, "-j", "DROP"])
    nsrun([IPT, "-A", "FORWARD", "-i", TUN, "-o", VETH_NS, "-j", "ACCEPT"])
    nsrun(
        [
            IPT,
            "-A",
            "FORWARD",
            "-i",
            VETH_NS,
            "-o",
            TUN,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        ]
    )
    nsrun(
        [
            IPT,
            "-t",
            "nat",
            "-A",
            "POSTROUTING",
            "-s",
            "10.203.71.2/32",
            "-o",
            VETH_NS,
            "-j",
            "MASQUERADE",
        ]
    )
    nsrun(["/usr/sbin/sysctl", "-q", "-w", "net.ipv4.ip_forward=1"])
    for prefix, interface in (([], VETH), (["-n", NS], VETH_NS), (["-n", NS], "lo")):
        run([IP, *prefix, "link", "set", interface, "up"])
    run([IP, "-n", NS, "route", "add", "default", "via", "10.203.72.1"])
    checkpoint("core_start")
    awg = str((BIN / "awg").resolve())
    core = str((BIN / "amneziawg-go").resolve())
    process = subprocess.Popen(  # noqa: S603 - pinned binary; no shell; systemd cgroup owns child
        [IP, "netns", "exec", NS, core, "-f", TUN],
        env=ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        if SOCKET.exists():
            break
        if process.poll() is not None:
            raise SmokeError("awg_exited")
        time.sleep(0.05)
    else:
        raise SmokeError("uapi_timeout")
    checkpoint("ephemeral_config")
    key = run([awg, "genkey"]).strip()
    server_public = run([awg, "pubkey"], key + "\n").strip()
    protection = run([awg, "genkey"]).strip()
    phone_public = public_key(INPUT.read_text(encoding="ascii").strip())
    private_file(
        "server.conf",
        f"[Interface]\nPrivateKey = {key}\nListenPort = {PORT}\n"
        + AWG_PARAMETERS
        + f"HeaderProtectionKey = {protection}\n"
        + f"[Peer]\nPublicKey = {phone_public}\nAllowedIPs = 10.203.71.2/32\n",
    )
    nsrun([awg, "setconf", TUN, str(STATE / "server.conf")])
    (STATE / "server.conf").unlink()
    private_file(
        "phone.conf",
        "[Interface]\nAddress = 10.203.71.2/30\nDNS = 1.1.1.1\nMTU = 1280\n"
        + AWG_PARAMETERS
        + f"HeaderProtectionKey = {protection}\n"
        + f"[Peer]\nPublicKey = {server_public}\nEndpoint = {HOST}:{PORT}\n"
        + "AllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n",
    )
    del key, protection
    checkpoint("tunnel_interface_setup")
    run([IP, "-n", NS, "address", "add", "10.203.71.1/30", "dev", TUN])
    run([IP, "-n", NS, "link", "set", TUN, "mtu", "1280", "up"])
    print('{"endpoint_ready":true,"egress_verified":false}', flush=True)
    private_file(
        "ready.json",
        json.dumps(
            {
                "endpoint_ready": True,
                "native_pid": process.pid,
                "native_netns_inode": Path(f"/proc/{process.pid}/ns/net").stat().st_ino,
            }
        ),
    )
    while process.poll() is None:
        time.sleep(1)
    raise SmokeError("awg_exited")


def cleanup() -> bool:
    checkpoint("cleanup")
    trusted(STATE, directory=True)
    marker = STATE / "baseline.json"
    if not marker.exists():
        print('{"cleanup_no_mutation_marker":true}')
        return True  # Preflight failed: no ownership and no resources to remove.
    trusted(marker)
    saved = json.loads(marker.read_text(encoding="ascii"))
    legacy = saved.get("schema_version") != 2
    baseline = saved if legacy else saved["hashes"]
    ok = True
    # systemd killed the entire service cgroup before ExecStopPost. Remove DNAT
    # first, and keep attempting all cleanup operations after individual errors.
    rules = firewall_rules()
    for rule in sorted(rules, key=lambda item: item[1] != "PREROUTING"):
        try:
            probe = subprocess.run(  # noqa: S603 - exact reviewed rule, no shell
                rule_command("-C", rule),
                capture_output=True,
                env=ENV,
                timeout=2,
                check=False,
            )
            if probe.returncode == 0:
                run(rule_command("-D", rule))
            elif probe.returncode != 1:
                ok = False
        except (SmokeError, OSError, subprocess.SubprocessError):
            ok = False
    for path, argv in (
        (Path(f"/sys/class/net/{VETH}"), [IP, "link", "delete", VETH]),
        (Path(f"/run/netns/{NS}"), [IP, "netns", "delete", NS]),
    ):
        try:
            if path.exists():
                if path == Path(f"/run/netns/{NS}") and not owned_processes_gone():
                    ok = False  # Keep the namespace handle if service processes survive.
                    continue
                run(argv)
        except (SmokeError, OSError, subprocess.SubprocessError):
            ok = False
    for path in (SOCKET, STATE / "server.conf", STATE / "phone.conf", STATE / "ready.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            ok = False
    if I019 and HOST == "85.209.132.205":
        cleanup_i019_tables(saved["original_nft_tables"])
    current = snapshot(legacy=True) if legacy else snapshot()
    restored = {name: value == current.get(name) for name, value in baseline.items()}
    complete = ok and all(restored.values())
    print(json.dumps({"cleanup_complete": complete, "restored": restored}, sort_keys=True))
    if complete:
        marker.unlink()
    return complete


def recover() -> bool:
    """Standalone root recovery; never start a listener or restore foreign rules."""
    checkpoint("recovery")
    state = run(["/usr/bin/systemctl", "show", UNIT, "--property=ActiveState", "--value"]).strip()
    if state not in {"inactive", "failed"} or not owned_processes_gone():
        raise SmokeError("service_still_running")
    trusted(STATE, directory=True)
    marker = STATE / "baseline.json"
    if not marker.exists():
        print('{"recovery_not_needed":true}')
        return True
    trusted(marker)
    saved = json.loads(marker.read_text(encoding="ascii"))
    legacy = saved.get("schema_version") != 2
    if legacy and (
        set(saved)
        != {"filter_nat", "ipv6_filter", "routes4", "routes6", "links", "namespaces", "containers"}
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in saved.values()
        )
    ):
        raise SmokeError("unrecognized_baseline")
    if Path(f"/run/netns/{NS}").exists() and run([IP, "netns", "pids", NS]).strip():
        raise SmokeError("namespace_has_live_processes")
    complete = cleanup()
    if not legacy:
        return complete
    # v1 hashes cannot retrospectively remove counters/TTL. Keep the evidence;
    # never label the historical full snapshot verified or erase the failure.
    current = snapshot(legacy=True)
    if any(current[key] != saved[key] for key in ("containers", "links", "routes4")):
        raise SmokeError("stable_legacy_state_drift")
    if any(
        path.exists() for path in (Path(f"/run/netns/{NS}"), Path(f"/sys/class/net/{VETH}"), SOCKET)
    ):
        raise SmokeError("owned_resources_remain")
    if LABEL in run(["/usr/sbin/iptables-save"]):
        raise SmokeError("owned_rules_remain")
    if marker.exists():
        archive = INPUT.parent / "recovered-baseline-v1.json"
        descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            json.dump(saved, stream)
        marker.unlink()
    print(
        '{"owned_resources_removed":true,"historical_snapshot_fully_verified":false,"new_baseline_required":true}'
    )
    return True


def revoke() -> None:
    checkpoint("revoke_ownership")
    trusted(STATE / "baseline.json")
    trusted(STATE / "ready.json")
    trusted(INPUT)
    peer = public_key(INPUT.read_text(encoding="ascii").strip())
    # The service's private mount namespace owns the netns bind mount. An
    # external `ip netns exec` sees only its placeholder, not that namespace.
    # AWG's pathname UAPI socket is shared in /run and needs no setns at all.
    trusted(SOCKET.parent, directory=True)
    before = SOCKET.lstat()
    if before.st_uid != 0 or before.st_mode & 0o022 or not stat.S_ISSOCK(before.st_mode):
        raise SmokeError("untrusted_uapi_socket")
    awg = (BIN / "awg").resolve()
    trusted(awg)
    if hashlib.sha256(awg.read_bytes()).hexdigest() != HASHES["awg"]:
        raise SmokeError("artifact_hash_mismatch")
    checkpoint("revoke_peer_check")
    peers = run([str(awg), "show", TUN, "peers"]).split()
    if peers not in ([], [peer]):
        raise SmokeError("unexpected_peer_set")
    checkpoint("revoke_peer_remove")
    run([str(awg), "set", TUN, "peer", peer, "remove"])
    checkpoint("revoke_peer_verify")
    if run([str(awg), "show", TUN, "peers"]).strip():
        raise SmokeError("peer_still_present")
    after = SOCKET.lstat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise SmokeError("uapi_socket_changed")
    print('{"peer_removed":true,"traffic_cutoff_verified":false}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "serve", "cleanup", "recover", "revoke"))
    parser.add_argument("--i019-host", choices=("45.10.166.106", "85.209.132.205"))
    args = parser.parse_args()
    try:
        if args.i019_host:
            configure_i019(args.i019_host)
        require_environment(HOST, i019=I019)
        if args.action == "preflight":
            preflight(allow_disabled_forwarding=I019)
            print('{"preflight_passed":true,"network_changed":false}')
        elif args.action == "serve":
            serve()
        elif args.action == "cleanup":
            return 0 if cleanup() else 1
        elif args.action == "recover":
            return 0 if recover() else 1
        else:
            revoke()
        return 0
    except (
        SmokeError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        # No exception text, command output, arguments or config material.
        category = "check_rejected" if isinstance(exc, SmokeError) else "probe_unavailable"
        print(
            json.dumps(
                {
                    "error": "staging_window_failed",
                    "acceptance_passed": False,
                    "stage": CHECKPOINT,
                    "failure_code": category,
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

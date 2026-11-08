"""I-030 / ADR-086 control client: PC key + pinned amneziawg-go in WSL against one beta window.

Default mode is --self-test (no network, no WSL). The live run needs --execute and an owner
who is present for the bot code. Secrets are read hidden; tokens, keys, codes, endpoints and
raw responses are never printed. At most one window per run (ADR-086 allows two runs).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

HASHES = {
    "amneziawg-go": "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe",
    "awg": "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce",
}
BUDGET_BYTES = 16 * 1024 * 1024  # ADR-084 window size
WSL_DISTRO = "kali-linux"
SSH: dict[str, str] = {}  # set by --ssh-host: run the tunnel on that approved test host
IFACE = "awgctl0"
SERVER_TUNNEL_IP = "10.203.71.1"
CLIENT_TUNNEL_IP = "10.203.71.2"
KEEP = {
    "jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4",
    "i1", "i2", "i3", "i4", "i5", "headerprotectionkey",
}  # fmt: skip


class Failure(Exception):
    """Fixed diagnostics only."""


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def key_pair() -> tuple[str, str]:
    private = X25519PrivateKey.generate()
    return b64(private.private_bytes_raw()), b64(private.public_key().public_bytes_raw())


def key_proof(private_b64: str, ephemeral_public_b64: str, transcript: str) -> str:
    """Mirror of vpn_api.tunnels.admission.key_challenge."""
    shared = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64)).exchange(
        X25519PublicKey.from_public_bytes(base64.b64decode(ephemeral_public_b64))
    )
    return hmac.new(shared, transcript.encode("ascii"), hashlib.sha256).hexdigest()


def build_conf(client_config: str, private_b64: str) -> tuple[str, str]:
    """Return (awg setconf text, 'host:port' hidden from output). Tunnel routes only /32."""
    interface: list[str] = []
    peer: dict[str, str] = {}
    section = ""
    for raw in client_config.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line.lower()
        elif "=" in line:
            name, value = (part.strip() for part in line.split("=", 1))
            if section == "[interface]" and name.lower() in KEEP:
                interface.append(f"{name} = {value}")
            elif section == "[peer]":
                peer[name.lower()] = value
    if "publickey" not in peer or "endpoint" not in peer or not interface:
        raise Failure("client_config_shape_invalid")
    text = (
        "[Interface]\n"
        f"PrivateKey = {private_b64}\n"
        + "\n".join(interface)
        + "\n\n[Peer]\n"
        f"PublicKey = {peer['publickey']}\n"
        f"AllowedIPs = {SERVER_TUNNEL_IP}/32\n"
        f"Endpoint = {peer['endpoint']}\n"
        "PersistentKeepalive = 25\n"
    )
    return text, peer["endpoint"]


class Api:
    def __init__(self, base: str) -> None:
        target = urlsplit(base)
        local = target.hostname in {"127.0.0.1", "localhost"}
        if (target.scheme != "https" and not local) or target.path not in {"", "/"}:
            raise Failure("api_base_invalid")
        self.base = base.rstrip("/")
        self.access: str | None = None

    def call(self, method: str, path: str, body: dict | None = None, retries: int = 1) -> dict:
        for attempt in range(retries):
            request = urllib.request.Request(  # noqa: S310 - https or loopback checked above
                self.base + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={"Content-Type": "application/json"}
                | ({"Authorization": "Bearer " + self.access} if self.access else {}),
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                    content = response.read(256 * 1024)
                    return json.loads(content) if content else {}
            except urllib.error.HTTPError as error:
                code = ""
                try:
                    detail = json.loads(error.read(4096))
                    candidate = detail.get("code") or detail.get("error", {}).get("code", "")
                    code = candidate if re.fullmatch(r"[a-z_]{1,64}", str(candidate)) else ""
                except (ValueError, AttributeError):
                    pass
                raise Failure(f"http_{error.code}:{code or 'no_code'}") from None
            except (OSError, ValueError) as error:
                if attempt + 1 == retries:
                    # Exception class only (e.g. TimeoutError, ConnectionResetError), no text.
                    raise Failure(f"network_error:{type(error).__name__}") from None
                time.sleep(2 + attempt * 2)
        raise Failure("network_error")


def wsl(script: str, stdin: str | None = None, timeout: int = 90) -> str:
    """Run a literal bash script as root in WSL, or on the --ssh-host test server."""
    if SSH:
        argv = [
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=20", "-i", SSH["key"], "root@" + SSH["host"],
            "bash -c " + shlex.quote(script),
        ]  # fmt: skip
    else:
        argv = ["wsl.exe", "-d", WSL_DISTRO, "-u", "root", "--", "bash", "-c", script]
    for attempt in range(5 if SSH else 1):
        result = subprocess.run(  # noqa: S603 - fixed argv, script is a literal from this file
            argv,
            input=stdin.encode("utf-8") if stdin is not None else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 255:  # 255 = ssh connection failure, not a remote result
            break
        time.sleep(4 + attempt * 3)
    return result.stdout.decode("utf-8", errors="replace").replace("\x00", "")


def wsl_path(path: Path) -> str:
    drive = path.drive.rstrip(":").lower()
    return "/mnt/" + drive + "/" + "/".join(path.parts[1:]).replace("\\", "/")


def stage_binaries(bin_dir: Path) -> None:
    for name, expected in HASHES.items():
        actual = hashlib.sha256((bin_dir / name).read_bytes()).hexdigest()
        if actual != expected:
            raise Failure(f"binary_hash_mismatch:{name}")
    if SSH:
        if wsl("[ -e /root/awgctl ] && echo exists; true").strip():
            raise Failure("remote_dir_exists")  # never touch a directory we did not create
        wsl("set -e; umask 077; mkdir -p /root/awgctl /run/amneziawg")
        for attempt in range(5):
            copy = subprocess.run(  # noqa: S603 - fixed argv
                [
                    "scp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                    "-i", SSH["key"], str(bin_dir / "amneziawg-go"), str(bin_dir / "awg"),
                    "root@" + SSH["host"] + ":/root/awgctl/",
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )  # fmt: skip
            if copy.returncode == 0:
                break
            time.sleep(4 + attempt * 3)
        if copy.returncode != 0:
            raise Failure("scp_failed")
        wsl("chmod 700 /root/awgctl/amneziawg-go /root/awgctl/awg")
        out = wsl("cd /root/awgctl && sha256sum amneziawg-go awg")
    else:
        source = wsl_path(bin_dir)
        out = wsl(
            "set -e; umask 077; mkdir -p /root/awgctl /run/amneziawg; "
            f"cp '{source}/amneziawg-go' '{source}/awg' /root/awgctl/; "
            "chmod 700 /root/awgctl/*; cd /root/awgctl; sha256sum amneziawg-go awg"
        )
    for name, expected in HASHES.items():
        if f"{expected}  {name}" not in out:
            raise Failure(f"binary_hash_mismatch_on_host:{name}")


def stop_wsl_tunnel(remove_dir: bool = False) -> None:
    # '[a]' keeps pkill from matching this very bash command line.
    script = (
        f"pkill -f '[a]mneziawg-go -f {IFACE}'; sleep 0.5; ip link del {IFACE} 2>/dev/null; "
        "rm -f /root/awgctl/c.conf; "
    )
    if remove_dir and SSH:
        script += "rm -rf /root/awgctl; rmdir /run/amneziawg 2>/dev/null; "
    wsl(script + "true")


def run_tunnel(conf: str, seconds: int) -> dict[str, object]:
    stop_wsl_tunnel()
    wsl(
        "set -e; umask 077; cat > /root/awgctl/c.conf; cd /root/awgctl; "
        f"(LOG_LEVEL=silent nohup ./amneziawg-go -f {IFACE} >/dev/null 2>&1 &) ; sleep 1; "
        f"./awg setconf {IFACE} c.conf; ip address add {CLIENT_TUNNEL_IP}/32 dev {IFACE}; "
        f"ip link set {IFACE} mtu 1280 up; ip route add {SERVER_TUNNEL_IP}/32 dev {IFACE}",
        stdin=conf,
    )
    result: dict[str, object] = {"handshake": False, "rx": 0, "tx": 0, "samples": []}
    count = max(3, seconds // 3)
    out = wsl(
        f"for i in $(seq 1 {count}); do "
        f"ping -c1 -W1 {SERVER_TUNNEL_IP} >/dev/null 2>&1; "
        f"echo S $(/root/awgctl/awg show {IFACE} dump | sed -n 2p | tr '\t' ' '); sleep 2; done",
        timeout=seconds + 120,
    )
    for line in out.splitlines():
        fields = line.split()
        # 'S' pub psk endpoint allowed-ips latest-handshake rx tx keepalive
        if fields[:1] == ["S"] and len(fields) >= 8:
            shake, rx, tx = int(fields[5] != "0"), int(fields[6]), int(fields[7])
            result["samples"].append((shake, rx, tx))
            result["handshake"] = result["handshake"] or bool(shake)
            result["rx"], result["tx"] = rx, tx
    return result


def self_test() -> int:
    private, public = key_pair()
    ephemeral = X25519PrivateKey.generate()
    transcript = "project-vpn:key-confirmation:v1:" + uuid4().hex + ":" + "ab" * 32
    # Server side recomputes with the ephemeral private key and the device public key.
    server = hmac.new(
        ephemeral.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(public))),
        transcript.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    client = key_proof(private, b64(ephemeral.public_key().public_bytes_raw()), transcript)
    assert server == client and len(client) == 64, "proof mismatch"
    sample = (
        "[Interface]\nAddress = 10.203.71.2/30\nDNS = 1.1.1.1\nMTU = 1280\n"
        "Jc = 4\nJmin = 64\nJmax = 96\nS1 = 16\nS2 = 24\nS3 = 32\nS4 = 40\n"
        "H1 = 101234\nH2 = 202345\nH3 = 303456\nH4 = 404567\n"
        "HeaderProtectionKey = " + b64(bytes(range(32))) + "\n[Peer]\nPublicKey = "
        + b64(bytes(range(1, 33))) + "\nEndpoint = 203.0.113.9:51901\n"
        "AllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n"
    )  # fmt: skip
    conf, endpoint = build_conf(sample, private)
    assert "0.0.0.0/0" not in conf and "DNS" not in conf and "Address" not in conf
    assert "HeaderProtectionKey" in conf and "S4 = 40" in conf and endpoint.endswith(":51901")
    print("self_test=pass (key proof round-trip, profile conversion, /32-only route)")
    return 0


def execute(args: argparse.Namespace) -> int:
    api = Api(args.api)
    bin_dir = Path(args.bin_dir).resolve()
    stage_binaries(bin_dir)
    private, public = key_pair()
    device_id = window_id = None
    outcome: dict[str, object] = {}
    try:
        start = api.call("POST", "/api/v1/auth/start", retries=3)
        print("Open this bot link, request a code, then return here:")
        print(start["bot_url"])
        code = getpass.getpass("Code from the bot (hidden): ")
        tokens = api.call(
            "POST",
            "/api/v1/auth/verify",
            {"request_id": start["request_id"], "verifier": start["verifier"], "code": code},
            retries=4,
        )
        code = ""
        api.access = tokens["access_token"]
        device = api.call(
            "POST",
            "/api/v1/devices",
            {"platform": "android", "key_type": "x25519", "public_key": public},
            retries=4,
        )
        device_id = device["id"]
        nodes = api.call("GET", "/api/v1/window-tunnels/nodes", retries=4)["nodes"]
        node = next((n for n in nodes if n["available"]), None)
        if node is None:
            raise Failure("no_available_node")
        challenge = api.call("POST", f"/api/v1/tunnels/key-challenge/{device_id}", retries=4)
        proof = key_proof(private, challenge["ephemeral_public_key"], challenge["transcript"])
        window = api.call(
            "POST",
            "/api/v1/window-tunnels",
            {
                "device_id": device_id,
                "node_id": node["id"],
                "idempotency_key": str(uuid4()),
                "budget_bytes": BUDGET_BYTES,
                "proof_id": challenge["id"],
                "proof_response": proof,
            },
            retries=3,  # idempotency_key is fixed in this body, so a retry cannot double-open
        )
        window_id = window["id"]
        config = window.get("client_config")
        for _ in range(20):
            if config:
                break
            time.sleep(3)
            view = api.call("GET", f"/api/v1/window-tunnels/{window_id}", retries=4)
            config = view.get("client_config")
        if not config:
            raise Failure("window_not_active")
        conf, _endpoint = build_conf(config, private)
        outcome = run_tunnel(conf, args.seconds)
    finally:
        stop_wsl_tunnel(remove_dir=True)
        if window_id:
            try:
                api.call("DELETE", f"/api/v1/window-tunnels/{window_id}", retries=3)
            except Failure as error:
                print(f"cleanup_window_failed={error}")
        if device_id:
            try:
                api.call("DELETE", f"/api/v1/devices/{device_id}", retries=3)
            except Failure as error:
                print(f"cleanup_device_failed={error}")
    print(
        f"control_client_result handshake={str(outcome.get('handshake', False)).lower()}; "
        f"rx_bytes={outcome.get('rx', 0)}; tx_bytes={outcome.get('tx', 0)}; "
        f"samples={outcome.get('samples', [])}"
    )
    return 0 if outcome.get("handshake") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="live run against the beta API")
    parser.add_argument("--api", default="https://193.68.88.239", help="beta API origin")
    parser.add_argument(
        "--bin-dir",
        default=r"C:\Users\JetGame\OneDrive\Документы\ChatGPT\DetourVpn\.cache\beta-node-v3-20261025\bin",
    )
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--ssh-host", help="run the tunnel on this approved test host (root, key)")
    parser.add_argument("--ssh-key", default=str(Path.home() / ".ssh" / "selectel_key"))
    args = parser.parse_args()
    try:
        if not args.execute:
            return self_test()
        if not args.api:
            raise Failure("api_required")
        if args.ssh_host:
            if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}", args.ssh_host):
                raise Failure("ssh_host_invalid")
            SSH.update(host=args.ssh_host, key=args.ssh_key)
        return execute(args)
    except Failure as error:
        print(f"control_client_failed={error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

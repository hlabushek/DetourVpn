"""I-032 stage 1 gate helper: run detour-engine.exe once against one beta window (owner only).

Run from an elevated PowerShell with --execute; the owner must be present for the bot code and
initiates this Connect. At most one window per run (16 MiB, same as the ADR-086 control client).
It reuses the control-client API plumbing, builds the engine config with the C++ tool (stdin only,
never a config file), holds the tunnel for --seconds, then stops the engine and deletes the window,
the device and every local file. Output is fixed markers only: no keys, addresses, config, engine
logs or visited names are printed or stored. The device is registered with platform "android"
because the backend has no "windows" platform until I-032 stage 5.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from scripts.i030_control_client import BUDGET_BYTES, Api, Failure, key_pair, key_proof

WORK = Path(os.environ.get("LOCALAPPDATA", ".")) / "ProjectVpn" / "i032-gate"


def self_test() -> int:
    """No network, no engine: argument and path plumbing only."""
    parsed = build_parser().parse_args(["--mode", "exclude", "--domain", "example.ru"])
    if parsed.mode != "exclude" or parsed.domain != ["example.ru"] or WORK.name != "i032-gate":
        raise Failure("self_test_failed")
    print("self_test=ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="live run against the beta API")
    parser.add_argument("--api", default="https://193.68.88.239", help="beta API origin")
    parser.add_argument("--engine", help="path to detour-engine.exe (next to wintun.dll)")
    parser.add_argument("--tool", help="path to detour_config_tool.exe")
    parser.add_argument("--mode", default="all", choices=["all", "exclude", "include"])
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--app", action="append", default=[])
    parser.add_argument("--seconds", type=int, default=90, help="how long to hold the tunnel")
    return parser


def api_ipv4(origin: str) -> str:
    host = urlsplit(origin).hostname or ""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        raise Failure("api_host_not_ipv4_literal") from None
    return str(address)


def run_engine(args: argparse.Namespace, profile: str, private: str, api_ip: str) -> str:
    run_dir = WORK / str(uuid4())
    run_dir.mkdir(parents=True)
    try:
        (run_dir / "profile.conf").write_text(profile, encoding="ascii")
        (run_dir / "device.key").write_text(private, encoding="ascii")
        tool_args = [
            args.tool, "--profile", str(run_dir / "profile.conf"),
            "--key-file", str(run_dir / "device.key"), "--api-ip", api_ip, "--mode", args.mode,
        ]  # fmt: skip
        for domain in args.domain:
            tool_args += ["--domain", domain]
        for app in args.app:
            tool_args += ["--app", app]
        built = subprocess.run(  # noqa: S603 - owner-supplied local tool, fixed arguments
            tool_args, capture_output=True, timeout=30, check=False
        )
        if built.returncode != 0:
            raise Failure(
                "config_build_failed:" + built.stderr.decode("ascii", "replace").strip()[:40]
            )
        engine = subprocess.Popen(  # noqa: S603
            [args.engine, "run", "-c", "stdin"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )  # fmt: skip
        if engine.stdin is None:
            raise Failure("engine_stdin_missing")
        engine.stdin.write(built.stdout)
        engine.stdin.close()
        built = None  # type: ignore[assignment]  # drop the config (it holds the device key)
        print(f"engine_started; hold_seconds={args.seconds}", flush=True)
        deadline = time.monotonic() + args.seconds
        outcome = "held"
        while time.monotonic() < deadline:
            if engine.poll() is not None:
                outcome = f"engine_exited_early code={engine.returncode}"
                break
            time.sleep(1)
        if engine.poll() is None:
            engine.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            try:
                engine.wait(timeout=20)
            except subprocess.TimeoutExpired:
                engine.kill()
                outcome += "; engine_killed"
        return outcome
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def execute(args: argparse.Namespace) -> int:
    if (
        not args.engine
        or not args.tool
        or not Path(args.engine).is_file()
        or not Path(args.tool).is_file()
    ):
        raise Failure("engine_or_tool_missing")
    if args.seconds < 20 or args.seconds > 600:
        raise Failure("seconds_out_of_range")
    api = Api(args.api)
    api_ip = api_ipv4(args.api)
    private, public = key_pair()
    device_id = window_id = None
    outcome = "not_run"
    try:
        start = api.call("POST", "/api/v1/auth/start", retries=3)
        print("Open this bot link, request a code, then return here:")
        print(start["bot_url"])
        code = getpass.getpass("Code from the bot (hidden): ")
        tokens = api.call(
            "POST", "/api/v1/auth/verify",
            {"request_id": start["request_id"], "verifier": start["verifier"], "code": code},
            retries=4,
        )  # fmt: skip
        code = ""
        api.access = tokens["access_token"]
        device = api.call(
            "POST", "/api/v1/devices",
            {"platform": "android", "key_type": "x25519", "public_key": public}, retries=4,
        )  # fmt: skip
        device_id = device["id"]
        nodes = api.call("GET", "/api/v1/window-tunnels/nodes", retries=4)["nodes"]
        node = next((n for n in nodes if n["available"]), None)
        if node is None:
            raise Failure("no_available_node")
        challenge = api.call("POST", f"/api/v1/tunnels/key-challenge/{device_id}", retries=4)
        proof = key_proof(private, challenge["ephemeral_public_key"], challenge["transcript"])
        window = api.call(
            "POST", "/api/v1/window-tunnels",
            {
                "device_id": device_id, "node_id": node["id"], "idempotency_key": str(uuid4()),
                "budget_bytes": BUDGET_BYTES, "proof_id": challenge["id"], "proof_response": proof,
            },
            retries=3,
        )  # fmt: skip
        window_id = window["id"]
        config = window.get("client_config")
        for _ in range(20):
            if config:
                break
            time.sleep(3)
            config = api.call("GET", f"/api/v1/window-tunnels/{window_id}", retries=4).get(
                "client_config"
            )
        if not config:
            raise Failure("window_not_active")
        outcome = run_engine(args, config, private, api_ip)
    finally:
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
    print(f"gate_run outcome={outcome}; confirm handshake and egress on the read-only peer status")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if not args.execute:
        return self_test()
    try:
        return execute(args)
    except Failure as error:
        print(f"failed={error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

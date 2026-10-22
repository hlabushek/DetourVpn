"""LOCAL WSL only. Reproduce the private mount/netns boundary with actual systemd/AWG."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from awg_local_smoke import ENV, IP, prepare_executables, require_environment, run

ROOT = Path(__file__).resolve().parents[1]
UNIT = "project-vpn-i012-local-core.service"
NS = "vpn-i012-private"
SOCKET = Path("/run/amneziawg/v11awg.sock")


def main() -> int:
    require_environment(None)
    if "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
        raise RuntimeError("local_wsl_only")
    if NS in run([IP, "netns", "list"]) or SOCKET.exists():
        raise RuntimeError("owned_runtime_exists")
    if (
        run(["/usr/bin/systemctl", "show", UNIT, "--property=LoadState", "--value"]).strip()
        != "not-found"
    ):
        raise RuntimeError("owned_unit_exists")
    baseline = run([IP, "-j", "route", "show", "table", "all"])
    base = Path("/run/project-vpn-i012-systemd")
    base.mkdir(mode=0o700, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="lab-", dir=base))
    fd: int | None = None
    report: dict[str, Any] = {}
    stage = "prepare"
    try:
        binaries = prepare_executables(ROOT / ".cache/i-010/artifacts", work)
        awg, core = binaries["awg"], binaries["amneziawg-go"]
        ready = work / "ready.json"
        worker = work / "worker.py"
        worker.write_text(
            "import json,pathlib,subprocess,time\n"
            + f"env={ENV!r}\n"
            + f"subprocess.run([{IP!r},'netns','add',{NS!r}],check=True,env=env)\n"
            + f"p=subprocess.Popen([{IP!r},'netns','exec',{NS!r},{core!r},'-f','v11awg'],env=env)\n"
            + f"while not pathlib.Path({str(SOCKET)!r}).exists():\n"
            + " if p.poll() is not None: raise RuntimeError('core_failed')\n"
            + " time.sleep(.05)\n"
            + f"pathlib.Path({str(ready)!r}).write_text(json.dumps("
            + "{'endpoint_ready':True,'native_pid':p.pid,"
            + "'native_netns_inode':pathlib.Path(f'/proc/{p.pid}/ns/net').stat().st_ino}))\n"
            + "p.wait()\n",
            encoding="ascii",
        )
        stage = "private_unit_start"
        run(
            [
                "/usr/bin/systemd-run",
                "--quiet",
                "--collect",
                f"--unit={UNIT}",
                "--property=Type=exec",
                "--property=RuntimeMaxSec=90",
                "--property=KillMode=control-group",
                "--property=User=root",
                "--property=UMask=0077",
                "--property=CapabilityBoundingSet=CAP_SYS_ADMIN CAP_NET_ADMIN CAP_NET_RAW",
                "--property=NoNewPrivileges=yes",
                "--property=PrivateTmp=yes",
                "--property=ProtectHome=yes",
                "--property=ProtectSystem=strict",
                "--property=ReadWritePaths=/run",
                "--property=StandardOutput=null",
                "--property=StandardError=null",
                "/usr/bin/python3",
                "-I",
                str(worker),
            ]
        )
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.1)
        if not ready.exists():
            raise RuntimeError("core_not_ready")
        stage = "reproduce_placeholder"
        failed = subprocess.run(  # noqa: S603 - fixed disposable local namespace
            [IP, "netns", "exec", NS, "/usr/sbin/nft", "-j", "list", "tables"],
            capture_output=True,
            env=ENV,
            timeout=5,
            check=False,
        )
        report["original_named_namespace_fails"] = failed.returncode != 0
        private_key = run([awg, "genkey"]).strip()
        server_public = run([awg, "pubkey"], private_key + "\n").strip()
        run(
            [awg, "setconf", "v11awg", "/dev/stdin"],
            f"[Interface]\nPrivateKey = {private_key}\nListenPort = 51892\n",
        )
        del private_key
        spec = importlib.util.spec_from_file_location(
            "local_agent", ROOT / "services/node-agent/agent.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("module_missing")
        agent: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        agent.ENDPOINT_READY = ready
        agent.ENDPOINT_CGROUP = f"0::/system.slice/{UNIT}"
        agent.AWG = Path(awg)
        stage = "pin_real_namespace"
        fd = agent.open_endpoint_namespace()
        report["verified_core_namespace_pinned"] = (
            os.fstat(fd).st_ino == json.loads(ready.read_text())["native_netns_inode"]
        )
        native = agent.Native(server_public, namespace_fd=fd)
        native.validate()
        stage = "apply_kernel_gate"
        native.initialize_gate()
        native.gate(65536, 2)
        time.sleep(3)
        data = json.loads(native.nft(["-j", "list", "set", "inet", "vpn_i012", "live"]))
        report["kernel_timeout_in_private_namespace"] = all(
            not row["set"].get("elem") for row in data["nftables"] if "set" in row
        )
        native.close_gate()
        stage = "agent_sandbox"
        sandbox_probe = work / "agent-sandbox.py"
        sandbox_result = work / "agent-sandbox-ok"
        agent_source = str(ROOT / "services/node-agent/agent.py")
        sandbox_probe.write_text(
            "import importlib.util,pathlib,os\n"
            + f"s=importlib.util.spec_from_file_location('a',{agent_source!r})\n"
            + "a=importlib.util.module_from_spec(s);s.loader.exec_module(a)\n"
            + f"a.ENDPOINT_READY=pathlib.Path({str(ready)!r})\n"
            + f"a.ENDPOINT_CGROUP={'0::/system.slice/' + UNIT!r}\n"
            + f"a.AWG=pathlib.Path({awg!r})\n"
            + "fd=a.open_endpoint_namespace()\n"
            + f"n=a.Native({server_public!r},namespace_fd=fd)\n"
            + "n.validate();n.close_gate();os.close(fd)\n"
            + f"pathlib.Path({str(sandbox_result)!r}).write_text('ok')\n",
            encoding="utf-8",
        )
        run(
            [
                "/usr/bin/systemd-run",
                "--quiet",
                "--wait",
                "--collect",
                "--property=RuntimeMaxSec=10",
                "--property=User=root",
                "--property=CapabilityBoundingSet=CAP_SYS_ADMIN CAP_NET_ADMIN CAP_NET_RAW",
                "--property=NoNewPrivileges=yes",
                "--property=PrivateTmp=yes",
                "--property=ProtectHome=yes",
                "--property=ProtectSystem=strict",
                "--property=ReadWritePaths=/run",
                "--property=StandardOutput=null",
                "--property=StandardError=null",
                "/usr/bin/python3",
                "-I",
                str(sandbox_probe),
            ]
        )
        report["separate_agent_sandbox_verified"] = sandbox_result.read_text() == "ok"
        report["host_namespace_unchanged"] = (
            os.fstat(fd).st_ino != Path("/proc/self/ns/net").stat().st_ino
        )
    except Exception as exc:
        report["failed_at"] = stage
        report["failure_type"] = type(exc).__name__
    finally:
        if fd is not None:
            os.close(fd)
        run(["/usr/bin/systemctl", "stop", UNIT], required=False)
        if SOCKET.exists():
            SOCKET.unlink()
        placeholder = Path("/run/netns") / NS
        if placeholder.exists():
            run([IP, "netns", "delete", NS], required=False)
        report["cleanup_complete"] = not SOCKET.exists() and not placeholder.exists()
        report["host_routes_unchanged"] = baseline == run(
            [IP, "-j", "route", "show", "table", "all"]
        )
        if work.resolve().parent == base.resolve() and work.name.startswith("lab-"):
            shutil.rmtree(work)
        print(json.dumps(report, sort_keys=True))
    return 0 if all(value is True for value in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

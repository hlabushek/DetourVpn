"""Local Linux/root lab only. Isolated netns, synthetic UDP; no AWG/VPN acceptance.

Run from WSL as root. Child namespaces disappear when their last descriptor/process
closes. No named netns, persistent interface, host firewall or remote changes.
"""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
SERVER = """
import json,signal,socket,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.bind(('127.0.0.1',int(sys.argv[1])))
count=0
def stop(*unused):
    print(json.dumps({'received':count}),flush=True)
    sys.exit(0)
signal.signal(signal.SIGTERM,stop)
print('ready',flush=True)
while True:
    try:
        data,remote=s.recvfrom(2000)
        count+=1
        s.sendto(data,remote)
    except (ConnectionRefusedError, PermissionError):
        pass
"""
CLIENT = """
import json,socket,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.settimeout(0.03)
received=0
for i in range(int(sys.argv[2])):
    s.sendto(b'x'*1000,('127.0.0.1',int(sys.argv[1])))
    try:
        if len(s.recv(2000))==1000: received+=1
    except TimeoutError: pass
print(json.dumps({'received':received}))
"""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "window_gate", ROOT / "services/node-agent/window_gate.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("gate_module_missing")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def check_private_journal() -> None:
    spec = importlib.util.spec_from_file_location(
        "window_journal", ROOT / "services/api/src/vpn_api/tunnels/window_journal.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("journal_module_missing")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with tempfile.TemporaryDirectory(prefix="detour-v3-journal-", dir="/root") as directory:
        path = Path(directory) / "state.db"
        with native.open_private_journal(path) as db:
            db.execute("CREATE TABLE facts (amount INTEGER)")
            with db:
                db.execute("INSERT INTO facts VALUES (123)")
            try:
                with native.open_private_journal(path):
                    raise RuntimeError("journal_concurrent_writer_allowed")
            except native.RuntimeFault as error:
                require(str(error) == "journal_already_running", "journal_lock_failed")
        with native.open_private_journal(path) as db:
            require(
                db.execute("SELECT amount FROM facts").fetchone()[0] == 123,
                "journal_persistence_failed",
            )
        linked = path.with_name("linked.db")
        linked.symlink_to(path)
        try:
            with native.open_private_journal(linked):
                raise RuntimeError("journal_symlink_allowed")
        except native.RuntimeFault:
            pass
        linked.unlink()
        os.link(path, linked)
        try:
            with native.open_private_journal(linked):
                raise RuntimeError("journal_hardlink_allowed")
        except native.RuntimeFault:
            pass
        linked.unlink()
        Path(directory).chmod(0o755)
        try:
            with native.open_private_journal(path):
                raise RuntimeError("journal_public_directory_allowed")
        except native.RuntimeFault:
            pass
        finally:
            Path(directory).chmod(0o700)


class Lab:
    def __init__(self, gate_module: ModuleType, slot: int) -> None:
        self.holder = subprocess.Popen(  # noqa: S603 - local test only, fixed child program
            [
                "/usr/bin/unshare",
                "-n",
                "--",
                "/usr/bin/python3",
                "-c",
                "import sys; print('ready',flush=True); sys.stdin.read()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=ENV,
        )
        self.fd = -1
        self.gate: Any = None
        self.server: subprocess.Popen[str] | None = None
        try:
            require(
                self.holder.stdout is not None and self.holder.stdout.readline() == "ready\n",
                "namespace_not_ready",
            )
            self.fd = os.open(f"/proc/{self.holder.pid}/ns/net", os.O_RDONLY)
            self.gate = gate_module.SlotGate(slot, self.fd)
            self.run(["/usr/sbin/ip", "link", "set", "lo", "up"])
            self.gate.initialize_closed()
            self.server = subprocess.Popen(  # noqa: S603 - isolated test namespace only
                self.argv(["/usr/bin/python3", "-u", "-c", SERVER, str(self.gate.port)]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                pass_fds=(self.fd,),
                env=ENV,
            )
            require(
                self.server.stdout is not None and self.server.stdout.readline() == "ready\n",
                "udp_server_not_ready",
            )
        except BaseException:
            self.dispose()
            raise

    def argv(self, command: list[str]) -> list[str]:
        return ["/usr/bin/nsenter", "--no-fork", f"--net=/proc/self/fd/{self.fd}", "--", *command]

    def run(self, command: list[str]) -> str:
        result = subprocess.run(  # noqa: S603 - local lab, fixed commands and validated integers
            self.argv(command),
            capture_output=True,
            text=True,
            timeout=15,
            pass_fds=(self.fd,),
            env=ENV,
            check=True,
        )
        return result.stdout

    def send(self, count: int) -> int:
        result = json.loads(
            self.run(["/usr/bin/python3", "-c", CLIENT, str(self.gate.port), str(count)])
        )
        return int(result["received"])

    def stop_server(self) -> int:
        require(self.server is not None, "server_absent")
        server = self.server
        if server is None:
            raise RuntimeError("server_absent")
        server.send_signal(signal.SIGTERM)
        output, error = server.communicate(timeout=5)
        self.server = None
        if not output:
            raise RuntimeError(f"TEST ONLY server exited {server.returncode}: {error}")
        return int(json.loads(output)["received"])

    def dispose(self) -> None:
        if self.server is not None:
            self.server.terminate()
            self.server.communicate(timeout=5)
            self.server = None
        if self.gate is not None:
            try:
                self.gate.close()
            finally:
                self.gate.dispose()
                self.gate = None
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        self.holder.communicate(timeout=5)


def main() -> None:
    require(sys.platform == "linux" and os.geteuid() == 0, "local_linux_root_required")
    native = module()
    labs: list[Lab] = []
    facts: dict[str, object] = {}
    try:
        check_private_journal()
        facts["private_journal_lock_links_permissions_persistence"] = True
        first = Lab(native, 1)
        labs.append(first)
        second = Lab(native, 2)
        labs.append(second)
        require(first.send(1) == second.send(1) == 0, "initial_gate_not_closed")
        facts["starts_closed"] = True
        first.gate.install_closed(65536)
        first.gate.open(5000)
        require(first.send(2) == 2, "opened_gate_not_live")
        require(second.send(1) == 0, "slot_isolation_failed")
        try:
            first.gate.closed_bytes()
        except native.GateError:
            pass
        else:
            raise RuntimeError("open_counter_accepted_as_final")
        first.gate.close()
        require(first.gate.closed_bytes() == 4 * 1028, "duplex_outer_ip_meter_inexact")
        require(first.send(1) == 0, "closed_gate_reopened")
        facts["duplex_outer_ip_bytes"] = 4 * 1028
        facts["slot_isolation"] = True
        second.gate.install_closed(65536)
        second.gate.open(250)
        require(second.send(1) == 1, "second_slot_not_live")
        # No worker checks expiry. Kernel timeout must expire while this process sleeps.
        time.sleep(0.35)
        require(second.send(1) == 0, "kernel_deadline_failed")
        require(not second.gate.is_live(), "expired_gate_reported_live")
        second.gate.close()
        require(second.gate.closed_bytes() == 2 * 1028, "expired_counter_changed")
        facts["independent_kernel_deadline"] = True
        first.gate.install_closed(65536)
        first.gate.open(5000)
        with ThreadPoolExecutor(max_workers=8) as pool:
            returned = sum(pool.map(first.send, [12] * 8))
        first.gate.close()
        measured = first.gate.closed_bytes()
        delivered = first.stop_server() - 2  # first duplex check above
        require(measured == (delivered + returned) * 1028, "packet_boundary_meter_inexact")
        require(0 < measured <= 65536, "concurrent_budget_exceeded")
        facts["concurrent_budget_bytes"] = measured
        facts["concurrent_boundary_exact"] = True
        second.gate.install_closed(65536)
        second.gate.open(5000)
        require(second.send(1) == 1, "surviving_slot_disrupted")
        second.gate.initialize_closed()  # process-restart primitive closes existing gate
        require(second.send(1) == 0, "restart_did_not_close_gate")
        facts["restart_closes_gate"] = True
    finally:
        for lab in reversed(labs):
            lab.dispose()
    print(
        json.dumps(
            {
                "scope": "local WSL Linux kernel, synthetic UDP, no AWG or VPN",
                "facts": facts,
                "namespace_processes_closed": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

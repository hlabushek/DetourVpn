"""Synthetic local WSL/SSH/sudo pipe lab. Never connects to a real server."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from check_i028_dns_observer import IP, QUERY, RESPONDER, setup_synthetic_network

RUN = Path("/run/i028-transport-lab")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def fixed_run(command: list[str]) -> None:
    subprocess.run(  # noqa: S603 - fixed local synthetic lab executables/arguments
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=ENV, timeout=10, check=True,
    )


def main() -> int:
    if sys.argv[1:] not in ([], ["--delayed-ready"]):
        return 2
    ready_delay = 9 if sys.argv[1:] else 0
    if os.geteuid() != 0 or any(
        Path(f"/proc/self/ns/{name}").stat().st_ino == Path(f"/proc/1/ns/{name}").stat().st_ino
        for name in ("net", "mnt")
    ):
        return 2
    fixed_run(["/usr/bin/mount", "--make-rprivate", "/"])
    fixed_run(["/usr/bin/mount", "-t", "tmpfs", "-o", "mode=0700,nosuid,nodev", "tmpfs", "/run"])
    fixed_run(["/usr/sbin/ip", "link", "set", "lo", "up"])
    setup_synthetic_network()
    RUN.mkdir(mode=0o700)
    Path("/run/sshd").mkdir(mode=0o755)
    for name in ("host", "client"):
        fixed_run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(RUN / name)])
    (RUN / "authorized_keys").write_text((RUN / "client.pub").read_text(), encoding="ascii")
    (RUN / "authorized_keys").chmod(0o600)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    pub = (RUN / "host.pub").read_text(encoding="ascii").strip()
    (RUN / "known_hosts").write_text(f"[127.0.0.1]:{port} {pub}\n", encoding="ascii")
    (RUN / "sshd_config").write_text(
        f"Port {port}\nListenAddress 127.0.0.1\nHostKey {RUN}/host\n"
        f"PidFile {RUN}/sshd.pid\nAuthorizedKeysFile {RUN}/authorized_keys\n"
        "PermitRootLogin yes\nPubkeyAuthentication yes\nPasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\nUsePAM yes\nStrictModes yes\n"
        "PermitUserEnvironment no\nAllowTcpForwarding no\nX11Forwarding no\n",
        encoding="ascii",
    )
    source = Path(__file__).resolve().parents[1] / "infra/staging-v3"
    for name in ("runtime.py", "dns_observer.py"):
        shutil.copyfile(source / name, RUN / name)
    # TEST ONLY: retain the real fixed operator/capture; replace its real staging
    # status with a synthetic live slot and emit one synthetic query after ready.
    helper = (
        "import os,sys,time,threading,subprocess\n"
        "from pathlib import Path\n"
        "import runtime\n"
        "runtime.ROOT=Path('/run/i028-transport-lab');runtime.RUN=runtime.ROOT\n"
        "runtime.status=lambda:{'prepared':True,'remaining_seconds':120,'live_slots':[1]}\n"
        "original_module=runtime.module\n"
        "def load(name):\n"
        " m=original_module(name)\n"
        " if name=='dns_observer':\n"
        "  original_observe=m.observe\n"
        "  def observe(slot,ready,stop):\n"
        "   def signal_ready():\n"
        f"    time.sleep({ready_delay})\n"
        "    ready()\n"
        "    def query():\n"
        "     time.sleep(0.15)\n"
        f"     subprocess.run([{IP!r},'netns','exec','dnsclient','/usr/bin/python3',"
        f"'-c',{QUERY!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=5)\n"
        "    threading.Thread(target=query,daemon=True).start()\n"
        "   return original_observe(slot,signal_ready,stop)\n"
        "  m.observe=observe\n"
        " return m\n"
        "runtime.module=load\n"
        "sys.argv=['fixed-test-helper','dns-observe']\n"
        "sys.exit(runtime.main())\n"
    )
    (RUN / "fixed_helper.py").write_text(helper, encoding="ascii")
    dns = subprocess.Popen(  # noqa: S603 - isolated synthetic DNS responder
        [IP, "netns", "exec", "dnswan", "/usr/bin/python3", "-c", RESPONDER],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV,
    )
    server = subprocess.Popen(  # noqa: S603 - private-netns loopback sshd, synthetic keys only
        ["/usr/sbin/sshd", "-D", "-e", "-f", str(RUN / "sshd_config")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV,
    )
    try:
        for _ in range(50):
            if server.poll() is not None:
                return 3
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            return 3
        command = [
            "/usr/bin/ssh", "-F", "/dev/null", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={RUN}/known_hosts",
            "-i", str(RUN / "client"), "-p", str(port), "root@127.0.0.1",
            "sudo -n /usr/bin/python3 /run/i028-transport-lab/fixed_helper.py",
        ]
        return subprocess.run(command, env=ENV, timeout=25, check=False).returncode  # noqa: S603
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)
        if dns.poll() is None:
            dns.terminate()
        try:
            dns.wait(timeout=3)
        except subprocess.TimeoutExpired:
            dns.kill()
            dns.wait(timeout=3)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError):
        raise SystemExit(4) from None

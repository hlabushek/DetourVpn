"""Run the suite against a temporary local PostgreSQL cluster; never use an existing DB."""

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def run_control(command: list[str], env: dict[str, str], log_path: Path) -> None:
    # PostgreSQL can inherit pipe handles on Windows; use a file, not capture_output.
    with log_path.open("ab") as output:
        result = subprocess.run(  # noqa: S603 - explicit binary path, fixed options, no shell.
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60,
        )
    if result.returncode:
        # Commands contain only paths/options; passwords exist only in a temporary file/env.
        print(log_path.read_bytes()[-4000:].decode("utf-8", errors="replace"), file=sys.stderr)
        raise RuntimeError(f"PostgreSQL control command failed: {Path(command[0]).name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    args = parser.parse_args()
    binary_dir = args.postgres_bin.resolve()
    suffix = ".exe" if os.name == "nt" else ""
    initdb = binary_dir / ("initdb" + suffix)
    pg_ctl = binary_dir / ("pg_ctl" + suffix)
    if not initdb.is_file() or not pg_ctl.is_file():
        parser.error("--postgres-bin must contain initdb and pg_ctl")

    cache = (ROOT / ".cache" / "postgres-tests").resolve()
    if not cache.is_relative_to(ROOT):
        raise RuntimeError("Test cluster cache must stay inside this workspace")
    cache.mkdir(parents=True, exist_ok=True)
    work = (cache / ("cluster-" + uuid4().hex)).resolve()
    if not work.is_relative_to(cache):
        raise RuntimeError("Unsafe temporary cluster path")
    work.mkdir(mode=0o700)
    data = work / "data"
    password_file = work / "password"
    control_log = work / "control.log"
    password = secrets.token_urlsafe(32)
    password_file.write_text(password, encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(binary_dir) + os.pathsep + env.get("PATH", "")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = False
    safe_to_remove = False
    try:
        run_control(
            [
                str(initdb),
                "-D",
                str(data),
                "-U",
                "vpn_i004_test",
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C",
                "--pwfile=" + str(password_file),
            ],
            env,
            control_log,
        )
        password_file.unlink()
        # No service registration. The listener is restricted to IPv4 loopback.
        run_control(
            [
                str(pg_ctl),
                "-D",
                str(data),
                "-l",
                str(work / "server.log"),
                "-o",
                f"-h 127.0.0.1 -p {port} -F -c log_statement=none",
                "-w",
                "-t",
                "20",
                "start",
            ],
            env,
            control_log,
        )
        started = True
        env["VPN_TEST_DATABASE_URL"] = (
            f"postgresql+psycopg://vpn_i004_test:{password}@127.0.0.1:{port}/postgres"
        )
        print("Running tests against an isolated real PostgreSQL server.", flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest"], cwd=ROOT, env=env, check=False)
        return result.returncode
    finally:
        try:
            # pg_ctl may have spawned a server even if startup timed out.
            if started or (data / "postmaster.pid").exists():
                run_control(
                    [str(pg_ctl), "-D", str(data), "-m", "fast", "-w", "-t", "20", "stop"],
                    env,
                    control_log,
                )
            safe_to_remove = True
        finally:
            if password_file.exists():
                password_file.unlink()
            # Verify the absolute deletion target immediately before recursive cleanup.
            target = work.resolve()
            if safe_to_remove and target.parent == cache and target.name.startswith("cluster-"):
                shutil.rmtree(target)
                print("Temporary PostgreSQL stopped; generated cluster removed.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

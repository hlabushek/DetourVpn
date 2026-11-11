"""Read-only: print the last minutes of window/renew errors from the beta API host, redacted.

Runs one fixed `journalctl | grep` over SSH with the same key and host as the deploy scripts.
Changes nothing. IPv4 addresses, long hex/base64 tokens and e-mail-like strings are masked
before printing, so the output can be pasted into a chat. Usage:

    uv run --frozen python scripts/beta_diag_logs.py [--minutes 20]
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts.deploy_beta_api_latest import HOST, IDENTITY

# Fixed remote command; the only variable part is an integer validated below.
REMOTE = (
    "journalctl -u detourvpn-beta-api.service --since '{minutes} min ago' --no-pager -o cat "
    "| grep -i -E 'window|renew|peer|pool|node_|error|exception|traceback|409|422|500|503' "
    "| tail -n 80"
)
MASKS = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"[A-Fa-f0-9]{40,}"), "<hex>"),
    (re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}"), "<token>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
]


def mask(line: str) -> str:
    for pattern, replacement in MASKS:
        line = pattern.sub(replacement, line)
    return line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.minutes <= 240:
        raise SystemExit("minutes must be 1..240")
    identity = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ssh" / IDENTITY.name
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(identity), f"root@{HOST}",
        REMOTE.format(minutes=args.minutes),
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)  # noqa: S603
    for line in result.stdout.splitlines():
        print(mask(line)[:300])
    if result.returncode not in (0, 1):  # grep exits 1 when nothing matched
        print(f"ssh_exit_code={result.returncode}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

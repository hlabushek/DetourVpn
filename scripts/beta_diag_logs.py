"""Read-only: print recent API/node logs and connection states from the beta hosts, redacted.

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

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # allow `python scripts/beta_diag_logs.py`

from scripts.deploy_beta_api_latest import HOST, IDENTITY  # noqa: E402

# Fixed remote command; the only variable part is an integer validated below.
REMOTE = (
    "journalctl -u detourvpn-beta-api.service --since '{minutes} min ago' --no-pager -o cat "
    "| grep -i -E 'window|renew|peer|pool|node_|error|exception|traceback|409|422|500|503' "
    "| tail -n 80"
)
REMOTE_ALL = (
    "echo api_unit=$(systemctl is-active detourvpn-beta-api.service); "
    "echo api_lines_last_window=$(journalctl -u detourvpn-beta-api.service --since '{minutes} min ago' "
    "--no-pager -o cat | wc -l); "
    "journalctl -u detourvpn-beta-api.service --since '{minutes} min ago' --no-pager -o cat | tail -n 40"
)
# Counts only, no identifiers: connection states of the last hour.
REMOTE_DB = (
    'docker exec detourvpn-beta-postgres-1 sh -c \'PGPASSWORD="$(cat /run/secrets/pg_runtime)" '
    "psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "
    '"SELECT state, count(*), max(created_at) FROM peer_connections '
    "WHERE created_at > now() - make_interval(hours => 1) GROUP BY state ORDER BY 3 DESC\"'"
)
NODE_REMOTE = (
    "for u in detourvpn-beta-node-agent.service detourvpn-beta-slot-bootstrap.service; do "
    "echo unit=$u state=$(systemctl is-active $u); done; "
    "journalctl -u 'detourvpn-beta-*' --since '{minutes} min ago' --no-pager -o cat | tail -n 60"
)
REMOTE_DB2 = (
    "docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD=\"$(cat /run/secrets/pg_runtime)\" "
    "psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "
    "\"SELECT to_char(c.created_at AT TIME ZONE 0, 'HH24:MI:SS') AS started, c.state, "
    "count(h.*) AS windows, max(h.generation) AS last_gen, "
    "round(extract(epoch FROM max(h.created_at) - min(h.created_at))) AS span_s "
    "FROM peer_connections c LEFT JOIN peer_window_holds h ON h.connection_id = c.id "
    "WHERE c.created_at > now() - make_interval(hours => 3) "
    "GROUP BY c.id, c.state, c.created_at ORDER BY c.created_at DESC LIMIT 25\"'"
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


def ssh_run(target: str, identity: Path, port: str, remote: str) -> list[str]:
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(identity), "-p", port, target, remote,
    ]  # fmt: skip
    for _attempt in range(3):  # the link to the host drops now and then (exit 255)
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)  # noqa: S603
        if result.returncode != 255:
            break
    lines = [mask(x)[:300] for x in result.stdout.splitlines()]
    if result.returncode not in (0, 1):
        lines.append(f"ssh_exit_code={result.returncode}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=20)
    parser.add_argument("--node", help="also read this node's agent logs (name from the inventory)")
    args = parser.parse_args()
    if not 1 <= args.minutes <= 240:
        raise SystemExit("minutes must be 1..240")
    identity = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ssh" / IDENTITY.name
    print("== API: filtered errors")
    print(
        chr(10).join(ssh_run(f"root@{HOST}", identity, "22", REMOTE.format(minutes=args.minutes)))
    )
    print("== API: unit state, line count, last 40 lines")
    print(
        chr(10).join(
            ssh_run(f"root@{HOST}", identity, "22", REMOTE_ALL.format(minutes=args.minutes))
        )
    )
    print("== DB: connections by state in the last hour (state|count|newest)")
    print(chr(10).join(ssh_run(f"root@{HOST}", identity, "22", REMOTE_DB)))
    print("== DB: per connection, last 3 h (started UTC|state|windows|last generation|seconds first-to-last window)")
    print(chr(10).join(ssh_run(f"root@{HOST}", identity, "22", REMOTE_DB2)))
    if args.node:
        from scripts.beta_nodes import load_inventory
        from scripts.update_beta_node_agent import DEFAULT_INVENTORY

        node = load_inventory(DEFAULT_INVENTORY)[args.node]
        print(f"== NODE {args.node}: agent and bootstrap")
        print(chr(10).join(ssh_run(
            f"{node.ssh_user}@{node.host}", node.ssh_key, str(node.ssh_port),
            NODE_REMOTE.format(minutes=args.minutes),
        )))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())

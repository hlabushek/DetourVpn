# ruff: noqa: E501
"""Read-only: connection and window history from the beta API host, for the reliability study.

Used by scripts/reliability_owner_run.ps1 (RELIABILITY_OWNER_LOGS.md). Same SSH key and host as
the deploy scripts and scripts/beta_diag_logs.py. Changes nothing:
  - the SQL runs in a read-only session (PGOPTIONS default_transaction_read_only=on, and inside
    BEGIN READ ONLY ... ROLLBACK);
  - the journal is only read (journalctl).
Identifiers are cut to 8 characters in SQL. IPv4 addresses, UUIDs, long tokens and e-mail-like
strings are masked here before anything is printed or written. Raw output is never stored.

    uv run --frozen python scripts/beta_reliability_logs.py [--hours 48] [--out <file>]
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.deploy_beta_api_latest import HOST, IDENTITY  # noqa: E402

CONTAINER = "detourvpn-beta-postgres-1"
# Fixed command; the SQL comes on stdin, so nothing user-supplied reaches the remote shell.
REMOTE_SQL = (
    f"docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' {CONTAINER} "
    'sh -c \'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api '
    '-d detourvpn_beta -v ON_ERROR_STOP=1 -AtqF "|"\''
)
REMOTE_JOURNAL = (
    "journalctl -u detourvpn-beta-api.service --since '{hours} hours ago' --no-pager "
    "-o short-iso | grep -F '/api/v1/window-tunnels' | tail -n 3000"
)

SQL = """BEGIN READ ONLY;
\\echo == devices with connections (device|platform|created UTC|revoked|connections)
SELECT left(d.id::text, 8), d.platform, to_char(d.created_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI'),
       d.revoked_at IS NOT NULL, count(c.id)
FROM devices d JOIN peer_connections c ON c.device_id = d.id
WHERE c.created_at > now() - make_interval(hours => {hours})
GROUP BY d.id ORDER BY max(c.created_at) DESC;
\\echo
\\echo == connections (device|conn|node|state|created UTC|windows|last gen|last window end UTC|closed UTC|lived s|ended by|native token)
SELECT left(c.device_id::text, 8), left(c.id::text, 8), left(c.node_id::text, 8), c.state,
       to_char(c.created_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'),
       count(h.id), max(h.generation),
       to_char(max(h.not_after) AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'),
       to_char(max(r.created_at) AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'),
       round(extract(epoch FROM (max(r.created_at) - c.created_at))),
       CASE WHEN c.state <> 'closed' THEN 'open'
            WHEN max(r.created_at) >= max(h.not_after) - interval '10 seconds' THEN 'window_end'
            ELSE 'revoked_early' END,
       c.renew_token_digest IS NOT NULL
FROM peer_connections c
LEFT JOIN peer_window_holds h ON h.connection_id = c.id
LEFT JOIN peer_window_receipts r ON r.window_id = h.id
WHERE c.created_at > now() - make_interval(hours => {hours})
GROUP BY c.id ORDER BY c.created_at;
\\echo
\\echo == short-lived connections: closed within 60 s with one window (device|conn|created UTC|lived s|next conn of same device starts after s)
SELECT left(device_id::text, 8), left(id::text, 8),
       to_char(created_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'), lived, next_after
FROM (
  SELECT c.device_id, c.id, c.created_at, c.state,
         round(extract(epoch FROM (max(r.created_at) - c.created_at))) AS lived,
         count(h.id) AS windows,
         round(extract(epoch FROM (lead(c.created_at) OVER (PARTITION BY c.device_id
               ORDER BY c.created_at) - c.created_at))) AS next_after
  FROM peer_connections c
  LEFT JOIN peer_window_holds h ON h.connection_id = c.id
  LEFT JOIN peer_window_receipts r ON r.window_id = h.id
  WHERE c.created_at > now() - make_interval(hours => {hours})
  GROUP BY c.id
) x
WHERE state = 'closed' AND windows = 1 AND lived < 60 ORDER BY created_at;
\\echo
\\echo == windows (conn|gen|issued UTC|end UTC|window s|closed UTC|closed after end s|observation)
SELECT left(h.connection_id::text, 8), h.generation,
       to_char(h.created_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'),
       to_char(h.not_after AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'),
       round(extract(epoch FROM (h.not_after - h.created_at))),
       to_char(r.created_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS'),
       round(extract(epoch FROM (r.created_at - h.not_after))), r.observation
FROM peer_window_holds h LEFT JOIN peer_window_receipts r ON r.window_id = h.id
WHERE h.created_at > now() - make_interval(hours => {hours})
ORDER BY h.created_at LIMIT 600;
\\echo
\\echo == node runtime starts (node|runtime|started UTC)
SELECT left(node_id::text, 8), left(runtime_id::text, 8),
       to_char(created_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI:SS')
FROM peer_runtime_epochs WHERE created_at > now() - make_interval(hours => {hours})
ORDER BY created_at;
ROLLBACK;
"""

MASKS = [
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<id>",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"[A-Fa-f0-9]{40,}"), "<hex>"),
    (re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}"), "<token>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
]
# uvicorn access line: ... "POST /api/v1/window-tunnels/<id>/native-renew HTTP/1.1" 202 ...
ACCESS = re.compile(
    r'^(?P<time>\S+)\s.*"(?P<method>GET|POST|DELETE) (?P<path>/api/v1/window-tunnels[^ "?]*)'
    r'[^"]*" (?P<status>\d{3})'
)


def mask(line: str) -> str:
    for pattern, replacement in MASKS:
        line = pattern.sub(replacement, line)
    return line[:300]


def ssh(remote: str, stdin: str | None = None) -> tuple[int, str]:
    identity = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".ssh" / IDENTITY.name
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(identity), f"root@{HOST}", remote,
    ]  # fmt: skip
    result = None
    for _attempt in range(3):  # the link to the host drops now and then (exit 255)
        result = subprocess.run(  # noqa: S603 - fixed command, no shell
            command, input=stdin, capture_output=True, text=True, check=False, timeout=180
        )
        if result.returncode != 255:
            break
    if result is None:
        raise RuntimeError("ssh_not_run")
    if result.returncode == 255:
        SSH_ERRORS.append(ssh_reason(result.stderr))
    return result.returncode, result.stdout


SSH_ERRORS: list[str] = []


def ssh_reason(stderr: str) -> str:
    """Last SSH error line, without the host name, address or key path."""
    lines = [x.strip() for x in (stderr or "").splitlines() if x.strip()]
    line = lines[-1] if lines else "(ssh printed no reason)"
    line = (
        line.replace(HOST, "<host>").replace(str(IDENTITY), "<key>").replace(IDENTITY.name, "<key>")
    )
    return mask(line)


def journal_lines(hours: int) -> list[str]:
    code, out = ssh(REMOTE_JOURNAL.format(hours=hours))
    rows = []
    for raw in out.splitlines():
        match = ACCESS.search(raw)
        if match:
            path = MASKS[0][0].sub("<id>", match["path"])
            rows.append(f"{match['time']}|{match['method']}|{path}|{match['status']}")
    if not rows:
        rows.append("(no window-tunnel request lines found; the API may not log requests)")
    if code not in (0, 1):
        rows.append(f"ssh_exit_code={code}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--out", type=Path, help="also write the masked output to this file")
    args = parser.parse_args()
    if not 1 <= args.hours <= 72:
        raise SystemExit("hours must be 1..72")
    code, out = ssh(REMOTE_SQL, SQL.format(hours=args.hours))
    lines = [
        f"# beta reliability data, last {args.hours} h (read-only; IDs shortened, addresses masked)"
    ]
    lines += [mask(x) for x in out.splitlines()]
    if code != 0:
        lines.append(f"sql_exit_code={code}")
    if SSH_ERRORS:
        lines.append(f"ssh_error={SSH_ERRORS[-1]}")
    lines.append("")
    lines.append("== API window requests (time|method|path|status)")
    lines += [mask(x) for x in journal_lines(args.hours)]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

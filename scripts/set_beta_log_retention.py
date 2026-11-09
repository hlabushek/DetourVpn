"""Set the 7-day journald retention on the beta API host and every inventory node.

Dry run by default (read-only: prints, per host, the current drop-in state and disk usage).
With ``--execute`` it writes one drop-in ``/etc/systemd/journald.conf.d/detourvpn-retention.conf``
(``MaxRetentionSec=7day``) and restarts ``systemd-journald``. It never deletes existing logs;
``--vacuum`` additionally runs ``journalctl --vacuum-time=7d`` (removes entries older than 7 days)
and must be chosen explicitly. Only fixed markers are printed, never addresses or log lines.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.beta_nodes import InventoryError, load_inventory
from scripts.connect_beta_node import API_HOST, API_KEY

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "infra" / "beta" / "nodes.json"

REMOTE_PROGRAM = r"""
set -Eeuo pipefail
mode="$1"; vacuum="$2"
[[ "$mode" =~ ^(check|apply)$ ]] || exit 2
[[ "$vacuum" =~ ^(0|1)$ ]] || exit 2
file=/etc/systemd/journald.conf.d/detourvpn-retention.conf
want=$'[Journal]\nMaxRetentionSec=7day\n'
if [ -f "$file" ] && [ "$(cat "$file")" = "${want%$'\n'}" ]; then echo "dropin=present"; else echo "dropin=absent"; fi
echo "disk=$(journalctl --disk-usage 2>/dev/null | grep -oE '[0-9.]+[KMGT]' | head -1)"
if [ "$mode" = apply ]; then
  install -d -m 0755 /etc/systemd/journald.conf.d
  tmp="$(mktemp /etc/systemd/journald.conf.d/.detourvpn-retention.XXXXXX)"
  printf '%s' "$want" > "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$file"
  systemctl restart systemd-journald
  systemctl is-active --quiet systemd-journald
  [ "$(cat "$file")" = "${want%$'\n'}" ]
  echo "applied=1"
  if [ "$vacuum" = 1 ]; then journalctl --vacuum-time=7d >/dev/null 2>&1; echo "vacuumed=1"; fi
fi
"""


def run_host(host: str, key: Path, port: int, mode: str, vacuum: bool) -> dict[str, str]:
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(key), "-p", str(port), f"root@{host}",
        "bash", "-s", "--", mode, "1" if vacuum else "0",
    ]  # fmt: skip
    try:
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no local shell, fixed program on stdin.
            command,
            input=REMOTE_PROGRAM.encode("ascii"),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"error": "ssh_unavailable"}
    if result.returncode != 0:
        return {"error": f"remote_failed_{result.returncode}"}
    values: dict[str, str] = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        name, separator, value = line.partition("=")
        if separator and name in {"dropin", "disk", "applied", "vacuumed"} and len(value) <= 12:
            values[name] = value
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--execute", action="store_true", help="really write the drop-in")
    parser.add_argument(
        "--vacuum", action="store_true", help="also delete entries older than 7 days"
    )
    arguments = parser.parse_args(argv)
    if arguments.vacuum and not arguments.execute:
        print(json.dumps({"ok": False, "error": "vacuum_requires_execute"}), file=sys.stderr)
        return 1
    try:
        nodes = load_inventory(arguments.inventory)
    except InventoryError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    mode = "apply" if arguments.execute else "check"
    targets = [("api-host", API_HOST, API_KEY, 22)] + [
        (name, node.host, node.ssh_key, node.ssh_port) for name, node in nodes.items()
    ]
    report = {
        label: run_host(host, key, port, mode, arguments.vacuum)
        for label, host, key, port in targets
    }
    print(json.dumps({"mode": mode, "hosts": report}, indent=2, sort_keys=True))
    failed = any(
        "error" in value or (arguments.execute and value.get("applied") != "1")
        for value in report.values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

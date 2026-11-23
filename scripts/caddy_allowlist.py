"""Merge one node IP into the live beta Caddyfile's node allowlist (audit M5, ADR-175).

Used by scripts/activate_beta_caddy_allowlist.ps1. It reads the live configuration from
stdin (or a file), adds the requested node IP to the single ``remote_ip`` matcher, and
writes the result to stdout. Every IP already in the matcher is kept, so nodes that
scripts/connect_beta_node.py added earlier stay allowed. Idempotent: running it again with
the same IP returns the input unchanged.

Fails closed (non-zero exit, fixed code on stderr) when the matcher is missing, duplicated
or malformed, or when an address is not an IPv4 address.
"""

from __future__ import annotations

import ipaddress
import re
import sys

CODE = re.compile(r"[a-z_]{1,64}")


class AllowlistError(ValueError):
    """Fixed codes only."""


def merge(live: str, node_ip: str) -> str:
    try:
        wanted = ipaddress.IPv4Address(node_ip)
    except ValueError:
        raise AllowlistError("node_ip_invalid") from None
    lines = live.split("\n")
    hits = [index for index, line in enumerate(lines) if line.split()[:1] == ["remote_ip"]]
    if len(hits) != 1:
        raise AllowlistError("caddy_remote_ip_not_unique")
    parts = lines[hits[0]].split()
    if len(parts) < 2:
        raise AllowlistError("caddy_remote_ip_empty")
    current: list[str] = []
    for value in parts[1:]:
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            # A placeholder or an IPv6 entry must not be silently kept or dropped.
            raise AllowlistError("caddy_remote_ip_invalid") from None
        if str(address) not in current:
            current.append(str(address))
    if str(wanted) not in current:
        current.append(str(wanted))
    indent = lines[hits[0]][: len(lines[hits[0]]) - len(lines[hits[0]].lstrip())]
    lines[hits[0]] = indent + "remote_ip " + " ".join(current)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "merge":
        print(
            "usage: caddy_allowlist.py merge <NODE_IP>  (live Caddyfile on stdin)", file=sys.stderr
        )
        return 2
    try:
        sys.stdout.write(merge(sys.stdin.read(), argv[1]))
    except AllowlistError as error:
        code = str(error) if CODE.fullmatch(str(error)) else "caddy_allowlist_failed"
        print(f"[caddy-allowlist] {code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Real nft cleanup test ONLY in a fresh local WSL network namespace.

Invoke through unshare --net. Never SSH to a VPS with this helper. No keys,
VPN peers, egress or iptables frontend are used; nft creates the table shape.
"""

import json
import os
import platform
from pathlib import Path

import android_staging_window as window
from awg_local_smoke import SmokeError, run


def main() -> None:
    if (
        getattr(os, "geteuid", lambda: -1)() != 0
        or "microsoft" not in platform.release().lower()
        or Path("/proc/self/ns/net").stat().st_ino == Path("/proc/1/ns/net").stat().st_ino
    ):
        raise RuntimeError("fresh_local_wsl_network_namespace_required")
    window.configure_i019("85.209.132.205")
    before = window.i019_tables()
    if before:
        raise RuntimeError("fresh_empty_namespace_required")
    for table, kind, chains in (
        (
            "filter",
            "filter",
            [("INPUT", "input", 0), ("FORWARD", "forward", 0), ("OUTPUT", "output", 0)],
        ),
        (
            "nat",
            "nat",
            [
                ("PREROUTING", "prerouting", -100),
                ("INPUT", "input", 100),
                ("OUTPUT", "output", -100),
                ("POSTROUTING", "postrouting", 100),
            ],
        ),
    ):
        run(["/usr/sbin/nft", "add", "table", "ip", table])
        for name, hook, priority in chains:
            run(
                [
                    "/usr/sbin/nft",
                    "add",
                    "chain",
                    "ip",
                    table,
                    name,
                    f"{{ type {kind} hook {hook} priority {priority}; policy accept; }}",
                ]
            )
    window.cleanup_i019_tables(before)
    if window.i019_tables() != before:
        raise RuntimeError("empty_table_cleanup_failed")
    run(["/usr/sbin/nft", "add", "table", "ip", "filter"])
    run(["/usr/sbin/nft", "add", "chain", "ip", "filter", "INPUT"])
    run(["/usr/sbin/nft", "add", "rule", "ip", "filter", "INPUT", "counter", "accept"])
    try:
        window.cleanup_i019_tables(before)
    except SmokeError:
        raw = run(["/usr/sbin/nft", "-j", "list", "table", "ip", "filter"])
        if not any("rule" in row for row in json.loads(raw)["nftables"]):
            raise RuntimeError("foreign_rule_removed") from None
    else:
        raise RuntimeError("foreign_rule_not_refused")
    print(
        json.dumps(
            {
                "local_namespace_only": True,
                "real_nft_tables": True,
                "iptables_frontend_tested": False,
                "empty_table_roundtrip": True,
                "foreign_rule_preserved": True,
            }
        )
    )


if __name__ == "__main__":
    main()

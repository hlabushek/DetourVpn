"""READ ONLY: lists tunnel rows that still block a node as "capacity" (catalog.py).

Usage (URL in VPN_DATABASE_URL, see the secure prompt in docs/runbooks/beta-backend-rollout-neon.md):
    python scripts\\tunnel_block_check.py [node_name_fragment]

Changes nothing. Prints the node, its status and heartbeat, and every tunnel row not in state
"revoked". Ids and user ids are printed so the owner can decide; no key, address or URL is printed.
"""

import os
import sys

import certifi
import psycopg


def main() -> int:
    url = os.environ.get("VPN_DATABASE_URL", "")
    if not url:
        print("VPN_DATABASE_URL is not set in this window.")
        return 2
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    fragment = sys.argv[1] if len(sys.argv) > 1 else "netherlands"
    sql = (
        "select n.name, n.status, n.last_seen_at, t.id, t.state, t.user_id "
        "from tunnels t join vpn_nodes n on n.id = t.node_id "
        "where n.name ilike %s and t.state <> 'revoked' "
        "order by n.name"
    )
    with psycopg.connect(url, sslrootcert=certifi.where(), connect_timeout=10) as conn:
        nodes = conn.execute(
            "select name, region, status, last_seen_at, peer_runtime_id is not null "
            "from vpn_nodes order by name"
        ).fetchall()
        rows = conn.execute(sql, (f"%{fragment}%",)).fetchall()
    print("all nodes (name, region, status, last_seen_at, has_runtime):")
    for node in nodes:
        print(node)
    if not rows:
        print(f"no non-revoked tunnel rows for nodes matching {fragment!r}")
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())

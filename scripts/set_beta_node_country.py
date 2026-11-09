"""One-off operator update (ADR-092): set public display country/city on the existing beta node.

Touches only vpn_nodes.country_code and vpn_nodes.city of the single active Helsinki row whose
country is still empty. Display metadata only: no credentials, endpoints or tunnel state.
Uses only the approved isolated-beta identity. Preflight (read-only) unless --execute is given.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

HOST = "193.68.88.239"
EXPECTED_HOST = "hiplet-137728"
IDENTITY = Path(os.environ["USERPROFILE"]) / ".ssh" / "detourvpn-beta-20261026"

SCRIPT = r"""set -Eeuo pipefail
set +x
execute="$1"
test "$(hostname -s)" = __HOST__
psql_runtime() { docker exec -i detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atq -v ON_ERROR_STOP=1 -f -'; }
psql_migrator() { docker exec -i detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_migrator)" psql -h 127.0.0.1 -U vpn_migrator -d detourvpn_beta -Atq -v ON_ERROR_STOP=1 -1 -f -'; }
rows="$(psql_runtime <<'SQL'
SELECT count(*) FROM vpn_nodes;
SQL
)"
test "$rows" = 1
state="$(psql_runtime <<'SQL'
SELECT status || '|' || region || '|' || coalesce(country_code, '-') || '|' || coalesce(city, '-') FROM vpn_nodes;
SQL
)"
echo before="$state"
test "$state" = 'active|helsinki|-|-'
if [ "$execute" != yes ]; then echo mode=preflight_only; exit 0; fi
updated="$(psql_migrator <<'SQL'
UPDATE vpn_nodes SET country_code = 'FI', city = 'Helsinki'
 WHERE region = 'helsinki' AND status = 'active' AND country_code IS NULL AND city IS NULL
RETURNING 1;
SQL
)"
test "$updated" = 1
echo after="$(psql_runtime <<'SQL'
SELECT status || '|' || region || '|' || coalesce(country_code, '-') || '|' || coalesce(city, '-') FROM vpn_nodes;
SQL
)"
echo result=success
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    ssh = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(IDENTITY), f"root@{HOST}",
        "bash", "-s", "--", "yes" if args.execute else "no",
    ]
    script = SCRIPT.replace("__HOST__", EXPECTED_HOST).replace("\r", "")
    result = subprocess.run(  # noqa: S603
        ssh, input=script.encode("utf-8"), capture_output=True, check=False
    )
    wanted = {"before", "after", "mode", "result"}
    out = result.stdout.decode("utf-8", "replace").splitlines()
    print("\n".join(x for x in out if x.split("=")[0] in wanted))
    print(f"exit_code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

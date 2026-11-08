"""I-030 / ADR-087 read-only node monitor: ONE ssh session, numbers only, no endpoints/keys.

Samples the beta slot namespace every 5 s: awg peer latest-handshake / rx / tx and the
gate counter `passed`. Optional --capture adds a header-only UDP capture (time and length
only, no addresses, no payload, no file) and needs the owner's explicit "yes" in chat.
It changes nothing on the node.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

HOST = "95.81.73.183"  # beta node, same constant as scripts/activate_beta_node.py
KEY = Path.home() / ".ssh" / "detourvpn-beta-20261026"
AWG = "/opt/detourvpn-node/current/awg"
SLOT_PORT = 51901

SAMPLER = r"""
ns=$(ip netns list | head -1 | cut -d' ' -f1)
[ -n "$ns" ] || { echo "E no_netns"; exit 0; }
end=$(( $(date +%s) + __SECONDS__ ))
while [ "$(date +%s)" -lt "$end" ]; do
  t=$(date +%s)
  peer=$(ip netns exec "$ns" __AWG__ show dv3s1 dump 2>/dev/null | sed -n 2p | awk -F'\t' '{print $5, $6, $7}')
  passed=$(ip netns exec "$ns" nft list counter inet detour_window_v3 passed 2>/dev/null \
    | awk '/packets/ {print $2, $4}')
  echo "S $t ${peer:-- - -} | ${passed:-- -}"
  sleep 5
done
"""

CAPTURE = r"""
timeout __SECONDS__ tcpdump -nn -tt -l -q -i any udp port __PORT__ 2>/dev/null \
  | awk '{print "C", int($1), $NF}' | head -400
"""


def run_remote(script: str, timeout: int) -> str:
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=20", "-i", str(KEY), f"root@{HOST}", "bash -s",
    ]  # fmt: skip
    for attempt in range(5):
        result = subprocess.run(  # noqa: S603 - fixed argv; script is a literal from this file
            argv, input=script.encode(), capture_output=True, timeout=timeout, check=False
        )
        if result.returncode != 255:  # 255 = connection failure (server resets rapid connects)
            return result.stdout.decode("utf-8", errors="replace")
        time.sleep(5 + attempt * 4)
    raise SystemExit("node_ssh_unavailable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=100)
    parser.add_argument("--capture", action="store_true", help="header-only capture (owner yes)")
    args = parser.parse_args()
    seconds = max(10, min(args.seconds, 300))
    script = SAMPLER.replace("__SECONDS__", str(seconds)).replace("__AWG__", AWG)
    if args.capture:
        script = (
            "( "
            + CAPTURE.replace("__SECONDS__", str(seconds)).replace("__PORT__", str(SLOT_PORT))
            + " ) & \n"
            + script
            + "\nwait\n"
        )
    out = run_remote(script, seconds + 90)
    keep = [line for line in out.splitlines() if re.fullmatch(r"[SCE] [0-9a-z_ |.:-]+", line)]
    base = None
    for line in keep:
        parts = line.split()
        if parts[0] in ("S", "C") and parts[1].isdigit():
            base = base or int(parts[1])
            parts[1] = f"+{int(parts[1]) - base}s"
            line = " ".join(parts)
        print(line)
    print(
        "legend: S +t latest_handshake_epoch rx_bytes tx_bytes | gate_passed_packets bytes ; "
        "C +t udp_payload_len ; handshake 0 = none"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

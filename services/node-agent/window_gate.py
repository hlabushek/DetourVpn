"""I-024 fixed nft operations in an operator-pinned slot netns.

Not a runnable remote agent or an AWG provisioner. The durable window state
machine must authorize an opening and persist the preceding closed receipt.
No command, executable, namespace or port is supplied by a remote grant.
"""

import json
import os
import subprocess

TABLE = "detour_window_v3"
NFT = "/usr/sbin/nft"
NSENTER = "/usr/bin/nsenter"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


class GateError(Exception):
    """Fixed diagnostics; no native stdout/stderr or untrusted payload echo."""


class SlotGate:
    def __init__(self, slot_id: int, namespace_fd: int) -> None:
        if type(slot_id) is not int or not 1 <= slot_id <= 16:
            raise GateError("invalid_slot")
        if type(namespace_fd) is not int or namespace_fd < 0:
            raise GateError("invalid_namespace")
        # Duplicate to pin lifetime; the caller must authenticate namespace ownership
        # against the fixed operator core process before constructing this object.
        self.fd = os.dup(namespace_fd)
        try:
            if os.fstat(self.fd).st_ino == os.stat("/proc/self/ns/net").st_ino:
                raise GateError("host_namespace_forbidden")
        except BaseException:
            os.close(self.fd)
            raise
        self.port = 51900 + slot_id

    def dispose(self) -> None:
        os.close(self.fd)

    def nft(self, arguments: list[str], program: str | None = None) -> str:
        result = subprocess.run(  # noqa: S603 - fixed executable/verbs, pinned fd, no shell
            [NSENTER, f"--net=/proc/self/fd/{self.fd}", "--", NFT, *arguments],
            input=program,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
            pass_fds=(self.fd,),
            env=ENV,
        )
        if result.returncode:
            raise GateError("kernel_gate_operation_failed")
        return result.stdout

    def program(self, budget: int, milliseconds: int, *, replace: bool) -> str:
        if type(budget) is not int or not 65536 <= budget <= 67108864:
            raise GateError("invalid_window_budget")
        if type(milliseconds) is not int or not 0 <= milliseconds <= 90000:
            raise GateError("invalid_window_duration")
        live = f"elements = {{ {self.port} timeout {milliseconds}ms }};" if milliseconds else ""
        prefix = f"delete table inet {TABLE}\n" if replace else ""
        return (
            prefix
            + f"""table inet {TABLE} {{
 quota budget {{ until {budget} bytes; }}
 counter passed {{ }}
 set live {{ type inet_service; flags timeout; {live} }}
 chain incoming {{ type filter hook input priority -50; policy accept;
  udp dport {self.port} udp dport @live quota name budget counter name passed accept
  udp dport {self.port} drop
 }}
 chain outgoing {{ type filter hook output priority -50; policy accept;
  udp sport {self.port} udp sport @live quota name budget counter name passed accept
  udp sport {self.port} drop
 }}
}}
"""
        )

    def initialize_closed(self) -> bool:
        tables = json.loads(self.nft(["-j", "list", "tables"]))
        exists = any(
            row.get("table", {}).get("family") == "inet"
            and row.get("table", {}).get("name") == TABLE
            for row in tables["nftables"]
        )
        if exists:
            self.close()
        else:
            self.nft(["-f", "-"], self.program(65536, 0, replace=False))
        return exists

    def is_live(self) -> bool:
        result = json.loads(self.nft(["-j", "list", "set", "inet", TABLE, "live"]))
        sets = [row["set"] for row in result["nftables"] if "set" in row]
        if len(sets) != 1:
            raise GateError("kernel_gate_state_invalid")
        return bool(sets[0].get("elem"))

    def install_closed(self, budget: int) -> None:
        """Reset only after the caller durably captured the old final receipt."""
        self.nft(["-f", "-"], self.program(budget, 0, replace=True))

    def open(self, milliseconds: int) -> None:
        """One-shot low-level action; caller enforces generation/replay/deadline.

        Must follow install_closed. Replays must never call this method to refresh TTL.
        """
        if type(milliseconds) is not int or not 1 <= milliseconds <= 90000:
            raise GateError("invalid_window_duration")
        self.nft(
            ["-f", "-"],
            f"add element inet {TABLE} live {{ {self.port} timeout {milliseconds}ms }}\n",
        )

    def close(self) -> None:
        # nft netlink commit completes the gate change before the subsequent read.
        self.nft(["flush", "set", "inet", TABLE, "live"])

    def closed_bytes(self) -> int:
        """Only a closed gate is evidence of a final, immutable counter snapshot."""
        result = json.loads(self.nft(["-j", "list", "table", "inet", TABLE]))
        live = [
            row["set"] for row in result["nftables"] if row.get("set", {}).get("name") == "live"
        ]
        counters = [
            row["counter"]
            for row in result["nftables"]
            if row.get("counter", {}).get("name") == "passed"
        ]
        if len(live) != 1 or live[0].get("elem") or len(counters) != 1:
            raise GateError("final_counter_requires_closed_gate")
        amount = counters[0].get("bytes")
        if type(amount) is not int or not 0 <= amount <= 67108864:
            raise GateError("kernel_meter_invalid")
        return amount

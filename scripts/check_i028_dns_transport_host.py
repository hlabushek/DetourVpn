"""Opt-in synthetic Windows→WSL→SSH→sudo operator protocol lab; no VPS/Android."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/api/tests"), str(ROOT / "services/api/src")]
from test_i028_live import collect_dns_path_observation  # noqa: E402


def main() -> int:
    lab = ROOT / "scripts/check_i028_dns_transport.py"
    wsl = str(Path(os.environ["SystemRoot"]) / "System32/wsl.exe")
    translated = subprocess.run(  # noqa: S603 - fixed WSL path conversion for local lab only.
        [wsl, "-d", "kali-linux", "--", "wslpath", "-a", lab.as_posix()],
        capture_output=True, check=True, timeout=10,
    ).stdout.decode("utf-8", errors="strict").strip()
    if not translated.startswith("/mnt/") or "\n" in translated:
        raise RuntimeError("local_lab_path_invalid")
    command = [
        wsl, "-d", "kali-linux", "-u", "root", "--", "unshare", "--mount", "--net",
        "--", "python3", translated,
    ]

    def probe(delay: float) -> dict[str, bool]:
        time.sleep(delay)
        return {"fixed_udp_dns_response": True}  # SYNTHETIC TEST INPUT ONLY.

    fast = collect_dns_path_observation(command, lambda: probe(1), finish_timeout=25)
    delayed = collect_dns_path_observation(
        [*command, "--delayed-ready"], lambda: probe(1), finish_timeout=25
    )
    slow = collect_dns_path_observation(command, lambda: probe(14), finish_timeout=25)
    expected = {"outbound": 1, "inbound": 1}
    passed = (
        fast.get("complete") is True
        and fast.get("counts") == {"tunnel": expected, "uplink": expected}
        and delayed.get("complete") is True
        and delayed.get("counts") == {"tunnel": expected, "uplink": expected}
        and slow.get("complete") is False
        and slow.get("stage") == "probe"
        and slow.get("reason") == "observer_ended_before_probe"
    )
    paths = (
        "scripts/check_i028_dns_transport.py",
        "scripts/check_i028_dns_observer.py",
        "scripts/check_i028_dns_transport_host.py",
        "infra/staging-v3/runtime.py",
        "infra/staging-v3/dns_observer.py",
        "services/api/tests/test_i028_live.py",
    )
    report = {
        "scope": "synthetic loopback SSH/sudo and private WSL namespaces; fake Android and status",
        "real_vps_or_android_test": False,
        "passed": passed,
        "ready_timeout_seconds": 30,
        "fixed_operator_and_capture_fast_result": fast,
        "delayed_ready_nine_seconds": delayed,
        "slow_probe_fails_closed": slow,
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        },
    }
    evidence = ROOT / "docs/evidence" / (
        f"i-028-dns-transport-local-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
    )
    evidence.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "evidence": evidence.name}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

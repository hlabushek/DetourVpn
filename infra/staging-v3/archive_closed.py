"""Root-only, fixed-path recoverable archive of an independently closed I-028 run.

No discarded artifacts, firewall mutation or journal interpretation. Live/ambiguous state
refuses archive. Used only by the owner's explicit --archive-closed installer.
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

SOURCES = {
    "release": Path("/opt/detourvpn-i028"),
    "node": Path("/opt/detourvpn-node"),
    "state": Path("/var/lib/detourvpn-node"),
    "run": Path("/run/detourvpn-i028"),
}
ARCHIVES = Path("/opt/detourvpn-i028-archives")
HELPER = Path("/usr/local/libexec/detourvpn-i028-control")
ABSENT = (
    Path("/run/detourvpn-node"),
    Path("/etc/detourvpn-node"),
    Path("/etc/sudoers.d/detourvpn-i028"),
)


class Refused(Exception):
    pass


def run(args: list[str], *, required: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed root preflight commands only.
        args,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if required and result.returncode:
        raise Refused("archive_preflight_command_failed")
    return result.stdout.decode("utf-8", "strict").strip()


def owned(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind or info.st_uid != 0 or info.st_mode & 0o022:
        raise Refused("archive_untrusted_path")


def archive_closed() -> Path:
    if "45.10.166.106/" not in run(["/usr/sbin/ip", "-4", "address", "show", "dev", "ens3"]):
        raise Refused("archive_wrong_host")
    for path in SOURCES.values():
        owned(path, directory=True)
    owned(HELPER, directory=False)
    marker = SOURCES["state"] / "i028-cleanup.json"
    owned(marker, directory=False)
    if (
        marker.stat().st_size > 2048
        or json.loads(marker.read_text()).get("cleanup_complete") is not True
    ):
        raise Refused("archive_without_cleanup_receipt")
    if any(path.exists() or path.is_symlink() for path in ABSENT):
        raise Refused("archive_with_runtime_or_grant")
    if any(
        (SOURCES["run"] / name).exists() or (SOURCES["run"] / name).is_symlink()
        for name in ("localhost.key", "localhost.crt", "slots.json")
    ):
        raise Refused("archive_with_runtime_keys")
    for name in ("agent", "relay", "core1", "core2", "timeout"):
        state = run(
            ["/usr/bin/systemctl", "is-active", "detourvpn-i028-" + name + ".service"],
            required=False,
        )
        if state not in ("inactive", "failed", "unknown"):
            raise Refused("archive_with_live_service")
    if any(
        row.split()[0].startswith("dv3slot")
        for row in run(["/usr/sbin/ip", "netns", "list"]).splitlines()
    ):
        raise Refused("archive_with_namespace")
    if "dv3h" in run(["/usr/sbin/ip", "-o", "link", "show"]):
        raise Refused("archive_with_link")
    if "DV3I028" in run(["/usr/sbin/iptables-save"]):
        raise Refused("archive_with_firewall_rules")
    if run(["/usr/bin/ss", "-H", "-lun", "sport = :51901 or sport = :51902"]):
        raise Refused("archive_with_udp_listener")
    if run(["/usr/bin/ss", "-H", "-ltn", "sport = :443 or sport = :18443"]):
        raise Refused("archive_with_tcp_listener")
    # The previous operator version left its independent timer after manual cleanup.
    # Disarm only after resources are independently verified closed; never stop the
    # timeout service here, which could itself be performing the final cleanup.
    timer = "detourvpn-i028-timeout.timer"
    run(["/usr/bin/systemctl", "stop", timer], required=False)
    if run(["/usr/bin/systemctl", "is-active", timer], required=False) not in (
        "inactive",
        "failed",
        "unknown",
    ):
        raise Refused("archive_with_active_timer")
    if not ARCHIVES.exists():
        ARCHIVES.mkdir(mode=0o700)
    owned(ARCHIVES, directory=True)
    if ARCHIVES.stat().st_mode & 0o077:
        raise Refused("archive_not_private")
    destination = Path(tempfile.mkdtemp(prefix="closed-", dir=ARCHIVES))
    # Fixed source set. /run can be a different filesystem: shutil.move preserves
    # links without following them and removes the source only after a full copy.
    # A partial failure retains data in the source/private destination for audit.
    shutil.copyfile(HELPER, destination / "control-wrapper")
    (destination / "control-wrapper").chmod(0o600)
    for label, source in SOURCES.items():
        shutil.move(str(source), str(destination / label))
    (destination / "archive.json").write_text(
        json.dumps(
            {
                "closed_resources_verified": True,
                "private_journal_preserved": True,
                "timeout_timer_disarmed": True,
                "sources": list(SOURCES),
            }
        )
        + "\n"
    )
    return destination


if __name__ == "__main__":
    try:
        if getattr(os, "geteuid", lambda: -1)() != 0:
            raise Refused("archive_root_required")
        archive_closed()
        print('{"closed_run_archived":true,"private_journal_preserved":true}')
    except Exception:
        print('{"error":"i028_closed_archive_refused"}')
        raise SystemExit(1) from None

"""Linux-only runtime cases inside check_v3_awg_slots' private AWG lab.

Real kernel/AWG/SQLite and SIGKILL. Grants are synthetic, not API/WAN acceptance.
"""

import os
import select
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from awg_local_smoke import SmokeError, run


def exercise(
    gates: list[Any],
    peers: list[Any],
    client_keys: list[str],
    server_keys: list[str],
    awg: str,
    work: Path,
    establish: Callable[[str], None],
    ping: Callable[..., None],
) -> dict[str, object]:
    if sys.platform != "linux":
        raise SmokeError("linux_runtime_lab_required")
    from vpn_api.tunnels.v3_contracts import WindowGrant
    from vpn_api.tunnels.window_journal import RuntimeFault, open_private_journal
    from vpn_api.tunnels.window_runtime import WindowJournal, WindowRuntime

    def reset_client(slot: int) -> None:
        run([awg, "set", f"dv3c{slot}", "peer", server_keys[slot - 1], "remove"])
        run(
            [
                awg,
                "set",
                f"dv3c{slot}",
                "peer",
                server_keys[slot - 1],
                "allowed-ips",
                "10.203.71.1/32",
                "endpoint",
                f"192.0.2.1:{51900 + slot}",
            ]
        )

    def grant(runtime: WindowRuntime, slot: int, seconds: int = 90) -> WindowGrant:
        now = datetime.now(UTC)
        return WindowGrant(
            connection_id=uuid4(),
            window_id=uuid4(),
            node_id=runtime.node_id,
            runtime_id=runtime.runtime_id,
            slot_id=slot,
            generation=1,
            public_key=client_keys[slot - 1],
            issued_at=now,
            not_after=now + timedelta(seconds=seconds),
            budget_bytes=1024 * 1024,
        )

    facts: dict[str, object] = {}
    with open_private_journal(work / "runtime.sqlite") as db:
        journal = WindowJournal(db)
        runtime = WindowRuntime(
            uuid4(), journal, {1: gates[0], 2: gates[1]}, {1: peers[0], 2: peers[1]}
        )
        runtime.startup()
        windows = []
        for slot in (1, 2):
            reset_client(slot)
            window = grant(runtime, slot)
            windows.append(window)
            runtime.apply(window)
        establish("dv3-client-1")
        establish("dv3-client-2")
        runtime.apply(windows[0])  # Native replay must not reconfigure the peer or reset counters.
        key_before = peers[0].inventory()
        now = datetime.now(UTC)
        next_window = WindowGrant.model_validate(
            {
                **windows[0].model_dump(),
                "window_id": uuid4(),
                "generation": 2,
                "issued_at": now,
                "not_after": now + timedelta(seconds=90),
            }
        )
        runtime.apply(next_window)
        receipt = journal.receipt(windows[0].window_id)
        if receipt is None or receipt.observation != "measured" or not receipt.observed_bytes:
            raise SmokeError("runtime_rotation_receipt_missing")
        journal.acknowledge(receipt)
        ping("dv3-client-1", allowed=True)
        if peers[0].inventory() != key_before:
            raise SmokeError("runtime_rotation_peer_changed")
        runtime.revoke(windows[0].connection_id)
        ping("dv3-client-1", allowed=False)
        ping("dv3-client-2", allowed=True)
        try:
            runtime.apply(next_window)
        except RuntimeFault:
            pass
        else:
            raise SmokeError("revoked_runtime_reopened")
        facts["runtime_two_slots_rotation_receipts_revoke"] = True
        restart = WindowRuntime(
            runtime.node_id, journal, {1: gates[0], 2: gates[1]}, {1: peers[0], 2: peers[1]}
        )
        restart.startup()
        final = journal.receipt(windows[1].window_id)
        if (
            final is None
            or final.observation != "measured"
            or not final.observed_bytes
            or restart.runtime_id == runtime.runtime_id
        ):
            raise SmokeError("runtime_restart_counter_not_preserved")
        facts["runtime_restart_closed_and_measured"] = True

    # More than the initial 90s authority, with four renewals of both real
    # tunnels. Synthetic local grants isolate dataplane/runtime behavior.
    with open_private_journal(work / "sustained.sqlite") as db:
        journal = WindowJournal(db)
        runtime = WindowRuntime(
            uuid4(), journal, {1: gates[0], 2: gates[1]}, {1: peers[0], 2: peers[1]}
        )
        runtime.startup()
        current_windows = []
        for slot in (1, 2):
            reset_client(slot)
            current = grant(runtime, slot)
            current_windows.append(current)
            runtime.apply(current)
        establish("dv3-client-1")
        establish("dv3-client-2")
        initial_inventory = [peer.inventory() for peer in peers]
        begun = time.monotonic()
        rotation_at = begun + 25
        rotations = 0
        receipts = []
        while time.monotonic() - begun < 105:
            for slot in (1, 2):
                ping(f"dv3-client-{slot}", allowed=True)
            if time.monotonic() >= rotation_at:
                for index, previous in enumerate(current_windows):
                    now = datetime.now(UTC)
                    current = WindowGrant.model_validate(
                        {
                            **previous.model_dump(),
                            "window_id": uuid4(),
                            "generation": previous.generation + 1,
                            "issued_at": now,
                            "not_after": now + timedelta(seconds=90),
                        }
                    )
                    runtime.apply(current)
                    receipt = journal.receipt(previous.window_id)
                    if receipt is None or not receipt.observed_bytes:
                        raise SmokeError("sustained_rotation_receipt_missing")
                    receipts.append(receipt)
                    journal.acknowledge(receipt)
                    current_windows[index] = current
                if [peer.inventory() for peer in peers] != initial_inventory:
                    raise SmokeError("sustained_rotation_changed_peer")
                rotations += 1
                rotation_at += 25
            time.sleep(0.5)
        for current in current_windows:
            runtime.revoke(current.connection_id)
            receipt = journal.receipt(current.window_id)
            if receipt is None or not receipt.observed_bytes:
                raise SmokeError("sustained_final_receipt_missing")
            receipts.append(receipt)
        if rotations != 4 or len({receipt.window_id for receipt in receipts}) != 10:
            raise SmokeError("sustained_rotation_count_invalid")
        facts["sustained_two_tunnels_seconds"] = int(time.monotonic() - begun)
        facts["sustained_renewals_per_tunnel"] = rotations
        facts["sustained_unique_measured_receipts"] = len(receipts)
        facts["sustained_measured_outer_bytes"] = sum(
            receipt.observed_bytes or 0 for receipt in receipts
        )

    for crash_in_open in (False, True):
        path = work / ("crash-intent.sqlite" if crash_in_open else "crash-active.sqlite")
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                with open_private_journal(path) as db:
                    machine = WindowRuntime(
                        uuid4(), WindowJournal(db), {1: gates[0]}, {1: peers[0]}
                    )
                    machine.startup()
                    if crash_in_open:
                        original = gates[0].open

                        def kill_after_kernel_open(
                            milliseconds: int, operation: Callable[[int], None] = original
                        ) -> None:
                            operation(milliseconds)
                            os.kill(os.getpid(), signal.SIGKILL)

                        gates[0].open = kill_after_kernel_open
                    machine.apply(grant(machine, 1, seconds=18))
                    os.write(write_fd, b"opened\n")
                    while True:
                        time.sleep(1)  # Parent explicitly SIGKILLs this runtime process.
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        reaped = False
        try:
            if not select.select([read_fd], [], [], 15)[0]:
                raise SmokeError("runtime_child_not_ready")
            ready = os.read(read_fd, 64)
            if not crash_in_open:
                if ready != b"opened\n":
                    raise SmokeError("runtime_child_start_failed")
                reset_client(1)
                establish("dv3-client-1")
                if not gates[0].is_live():
                    raise SmokeError("runtime_gate_expired_before_crash")
                os.kill(child, signal.SIGKILL)
            _, status = os.waitpid(child, 0)
            reaped = True
            if not os.WIFSIGNALED(status) or os.WTERMSIG(status) != signal.SIGKILL:
                raise SmokeError("runtime_child_not_killed")
            if not crash_in_open:
                deadline = time.monotonic() + 20
                while gates[0].is_live() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if gates[0].is_live():
                    raise SmokeError("runtime_crash_kernel_deadline_failed")
                ping("dv3-client-1", allowed=False)
            with open_private_journal(path) as db:
                journal = WindowJournal(db)
                interrupted = journal.current(1)
                if interrupted is None:
                    raise SmokeError("runtime_crash_journal_missing")
                recovered = WindowRuntime(
                    interrupted[0].node_id, journal, {1: gates[0]}, {1: peers[0]}
                )
                recovered.startup()
                receipt = journal.receipt(interrupted[0].window_id)
                expected = "unknown" if crash_in_open else "measured"
                if receipt is None or receipt.observation != expected:
                    raise SmokeError("runtime_crash_evidence_invalid")
                if crash_in_open and receipt.observed_bytes is not None:
                    raise SmokeError("runtime_crash_guessed_usage")
                if not crash_in_open and not receipt.observed_bytes:
                    raise SmokeError("runtime_crash_measured_counter_missing")
            facts[
                "sigkill_intent_preserves_unknown"
                if crash_in_open
                else "sigkill_active_kernel_expiry_measured_recovery"
            ] = True
        finally:
            os.close(read_fd)
            if not reaped:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(child, 0)
    return facts

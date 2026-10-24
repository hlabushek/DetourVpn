"""Node-side v3 state machine used by the fixed window daemon.

Fixed operator Gate/Peer implementations and authenticated dispatch are required.
Journal payloads contain public keys/IDs/counters only, never private tunnel keys.
"""

import math
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from vpn_api.tunnels.v3_contracts import (
    ClosedWindow,
    WindowGrant,
    validate_closed_window,
    validate_grant,
)
from vpn_api.tunnels.window_journal import RuntimeFault


class Gate(Protocol):
    def initialize_closed(self) -> bool: ...
    def install_closed(self, budget: int) -> None: ...
    def open(self, milliseconds: int) -> None: ...
    def close(self) -> None: ...
    def closed_bytes(self) -> int: ...
    def is_live(self) -> bool: ...


class Peer(Protocol):
    """Fixed operator slot; methods cannot select paths, namespaces or commands."""

    def install(self, public_key: str) -> None: ...
    def remove(self) -> None: ...


class WindowJournal:
    """SQLite FULL transactions; callers never hold them across native operations."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS slots (
                slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 16),
                grant_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('intent','active','closed'))
            );
            CREATE TABLE IF NOT EXISTS receipts (
                window_id TEXT PRIMARY KEY, slot INTEGER NOT NULL,
                payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
                CHECK(acknowledged IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS fences (connection_id TEXT PRIMARY KEY);
            CREATE TRIGGER IF NOT EXISTS immutable_receipt
                BEFORE UPDATE OF window_id, slot, payload ON receipts
                BEGIN SELECT RAISE(ABORT,'receipt_immutable'); END;
            CREATE TRIGGER IF NOT EXISTS receipt_no_delete
                BEFORE DELETE ON receipts
                BEGIN SELECT RAISE(ABORT,'receipt_immutable'); END;
        """)

    def current(self, slot: int) -> tuple[WindowGrant, str] | None:
        row = self.db.execute("SELECT grant_json,state FROM slots WHERE slot=?", (slot,)).fetchone()
        return (WindowGrant.model_validate_json(row[0]), row[1]) if row else None

    def save(self, grant: WindowGrant, state: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO slots(slot,grant_json,state) VALUES (?,?,?) ON CONFLICT(slot) "
                "DO UPDATE SET grant_json=excluded.grant_json,state=excluded.state",
                (grant.slot_id, grant.model_dump_json(), state),
            )

    def finish(self, grant: WindowGrant, receipt: ClosedWindow) -> None:
        validate_closed_window(receipt, grant)
        prior = self.db.execute(
            "SELECT payload FROM receipts WHERE window_id=?", (str(grant.window_id),)
        ).fetchone()
        if prior is not None and ClosedWindow.model_validate_json(prior[0]) != receipt:
            raise RuntimeFault("journal_receipt_conflict")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO receipts(window_id,slot,payload) VALUES (?,?,?)",
                (str(grant.window_id), grant.slot_id, receipt.model_dump_json()),
            )
            self.db.execute("UPDATE slots SET state='closed' WHERE slot=?", (grant.slot_id,))

    def pending(self, slot: int | None = None) -> list[ClosedWindow]:
        rows = self.db.execute(
            "SELECT payload FROM receipts WHERE acknowledged=0 AND (? IS NULL OR slot=?) "
            "ORDER BY rowid",
            (slot, slot),
        )
        return [ClosedWindow.model_validate_json(row[0]) for row in rows]

    def receipt(self, window_id: UUID) -> ClosedWindow | None:
        row = self.db.execute(
            "SELECT payload FROM receipts WHERE window_id=?", (str(window_id),)
        ).fetchone()
        return ClosedWindow.model_validate_json(row[0]) if row else None

    def acknowledge(self, receipt: ClosedWindow) -> None:
        prior = self.db.execute(
            "SELECT payload FROM receipts WHERE window_id=?", (str(receipt.window_id),)
        ).fetchone()
        if prior is None or ClosedWindow.model_validate_json(prior[0]) != receipt:
            raise RuntimeFault("journal_ack_conflict")
        with self.db:
            self.db.execute(
                "UPDATE receipts SET acknowledged=1 WHERE window_id=?", (str(receipt.window_id),)
            )

    def fence(self, connection_id: UUID) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO fences(connection_id) VALUES (?)", (str(connection_id),)
            )

    def fenced(self, connection_id: UUID) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM fences WHERE connection_id=?", (str(connection_id),)
            ).fetchone()
            is not None
        )


class WindowRuntime:
    def __init__(
        self,
        node_id: UUID,
        journal: WindowJournal,
        gates: dict[int, Gate],
        peers: dict[int, Peer],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            not gates
            or len(gates) > 16
            or gates.keys() != peers.keys()
            or any(type(slot) is not int or not 1 <= slot <= 16 for slot in gates)
        ):
            raise RuntimeFault("operator_slots_invalid")
        self.node_id, self.runtime_id = node_id, uuid4()
        self.journal, self.gates, self.peers, self.now = journal, gates, peers, now
        self.started = False
        self.admitted = False

    def _receipt(self, grant: WindowGrant, measured: int | None) -> ClosedWindow:
        return ClosedWindow(
            connection_id=grant.connection_id,
            window_id=grant.window_id,
            node_id=grant.node_id,
            runtime_id=grant.runtime_id,
            slot_id=grant.slot_id,
            generation=grant.generation,
            gate_closed=True,
            observation="unknown" if measured is None else "measured",
            meter="kernel-outer-ip.v1",
            observed_bytes=measured,
        )

    def _finish(self, slot: int, *, counter_exists: bool = True) -> None:
        gate = self.gates[slot]
        gate.close()
        current = self.journal.current(slot)
        if current is None or current[1] == "closed":
            return
        grant, state = current
        measured = None
        if counter_exists and state == "active":
            try:
                measured = gate.closed_bytes()
            except Exception:
                # Closure succeeded, but the meter is unavailable. Never guess a debit.
                measured = None
            if measured is not None and not 0 <= measured <= grant.budget_bytes:
                # Preserve uncertainty instead of clipping a faulty kernel counter.
                measured = None
        self.journal.finish(grant, self._receipt(grant, measured))

    def startup(self) -> None:
        if self.started:
            raise RuntimeFault("runtime_already_started")
        # Close ALL slots before reconciliation/removal or any possible new admission.
        existed = {}
        failed = False
        for slot, gate in self.gates.items():
            try:
                existed[slot] = gate.initialize_closed()
            except Exception:
                failed = True
        if failed:
            raise RuntimeFault("startup_gate_close_failed")
        for slot in self.gates:
            self._finish(slot, counter_exists=existed[slot])
            prior = self.journal.current(slot)
            if prior is not None:
                self.journal.fence(prior[0].connection_id)
            self.peers[slot].remove()
        self.started = True

    def apply(self, grant: WindowGrant) -> str:
        if not self.started or grant.slot_id not in self.gates:
            raise RuntimeFault("runtime_unavailable")
        if self.journal.fenced(grant.connection_id):
            raise RuntimeFault("connection_fenced")
        slot = grant.slot_id
        current = self.journal.current(slot)
        previous = current[0] if current and current[1] == "active" else None
        if previous is not None and not self.gates[slot].is_live():
            raise RuntimeFault("window_expired")
        for other in self.gates:
            row = self.journal.current(other)
            if other != slot and row is not None and row[0].connection_id == grant.connection_id:
                raise RuntimeFault("connection_slot_conflict")
        verdict = validate_grant(
            grant,
            node_id=self.node_id,
            runtime_id=self.runtime_id,
            slot_id=slot,
            now=self.now(),
            previous=previous,
        )
        if verdict == "replay":
            if not self.gates[slot].is_live():
                raise RuntimeFault("window_expired")
            return verdict
        if self.journal.pending(slot):
            raise RuntimeFault("receipt_ack_required")
        if previous is not None:
            self._finish(slot)
        else:
            # An old completed connection is never renewed under the new identity.
            if current is not None and current[0].connection_id == grant.connection_id:
                raise RuntimeFault("connection_fenced")
        self.journal.save(grant, "intent")
        self.admitted = True
        try:
            gate = self.gates[slot]
            gate.install_closed(grant.budget_bytes)
            if previous is None:
                self.peers[slot].install(grant.public_key)
            # At most 5s native operation plus the allowed 1s server-ahead clock skew.
            remaining = math.floor((grant.not_after - self.now()).total_seconds() * 1000) - 6000
            if not 1 <= remaining <= 90000:
                raise RuntimeFault("dispatch_deadline_exhausted")
            gate.open(remaining)
            self.journal.save(grant, "active")
        except Exception:
            self.journal.fence(grant.connection_id)
            self._finish(slot)
            self.peers[slot].remove()
            raise
        return "new"

    def revoke(self, connection_id: UUID) -> None:
        if not self.started:
            raise RuntimeFault("runtime_unavailable")
        self.journal.fence(connection_id)  # durable BEFORE the external operation
        for slot in self.gates:
            current = self.journal.current(slot)
            if current is not None and current[0].connection_id == connection_id:
                self._finish(slot)
                self.peers[slot].remove()

    def recover(self, grant: WindowGrant) -> ClosedWindow:
        """Authenticated old-epoch close command during startup reconciliation only."""
        if (
            not self.started
            or self.admitted
            or grant.node_id != self.node_id
            or grant.runtime_id == self.runtime_id
            or grant.slot_id not in self.gates
        ):
            raise RuntimeFault("recovery_phase_conflict")
        prior = self.journal.receipt(grant.window_id)
        if prior is not None:
            validate_closed_window(prior, grant)
            return prior
        self.journal.fence(grant.connection_id)
        self.gates[grant.slot_id].close()
        self.peers[grant.slot_id].remove()
        receipt = self._receipt(grant, None)
        self.journal.finish(grant, receipt)
        return receipt

    def close_grant(self, grant: WindowGrant) -> None:
        """Durable revoke of known or never-dispatched funded windows."""
        if (
            not self.started
            or grant.node_id != self.node_id
            or grant.runtime_id != self.runtime_id
            or grant.slot_id not in self.gates
        ):
            raise RuntimeFault("close_grant_binding_conflict")
        prior = self.journal.receipt(grant.window_id)
        if prior is not None:
            validate_closed_window(prior, grant)
            # A delayed revoke of a settled generation still fences and closes
            # any newer generation of the same connection.
            self.revoke(grant.connection_id)
            return
        current = self.journal.current(grant.slot_id)
        if (
            current is not None
            and current[1] != "closed"
            and current[0].connection_id != grant.connection_id
        ):
            raise RuntimeFault("close_grant_slot_conflict")
        self.revoke(grant.connection_id)
        if self.journal.receipt(grant.window_id) is None:
            self.gates[grant.slot_id].close()
            self.peers[grant.slot_id].remove()
            self.journal.finish(grant, self._receipt(grant, None))

    def expire(self) -> None:
        for slot in self.gates:
            current = self.journal.current(slot)
            if current and current[1] != "closed" and current[0].not_after <= self.now():
                self.revoke(current[0].connection_id)

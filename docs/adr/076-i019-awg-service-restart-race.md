# ADR-076 — Avoid duplicate AmneziaWG VPN service stop during reconnect

Status: implemented; one bounded physical drill passed 2026-11-01.

## Context

The 2026-11-01 independent-provider attempt successfully established fresh
handshakes and egress through both approved nodes on initial use and through A
after recovery. During the final reconnect to B, the agent acknowledged the new
peer as applied, while Android reported its VPN interface up but no handshake;
the independent literal-IP probe timed out. Cleanup completed on both hosts.

The pinned AmneziaWG `GoBackend.setState(..., DOWN, ...)` implementation already
stops its `VpnService` after bringing down an active tunnel. The application
adapter then also called `Context.stopService()` for the same service. On a
rapid manual reconnect this duplicate stop could race the next `UP` operation
against service destruction. The receipt does not prove that this race caused
the observed failure; it is a concrete, avoidable lifecycle race on the failing
path.

## Decision

Delegate active-tunnel shutdown to the pinned `GoBackend` implementation and
remove the adapter's duplicate `Context.stopService()` call. A disconnect when
the backend is already down remains a no-op, preserving the idle VPN service
used by the bounded protected API relay.

## Verification and limits

The pinned upstream implementation was inspected locally. Flutter analysis,
104 client tests, Android lint, debug/release builds and the independent-UID
probe build passed. The physical receipt then confirmed new B handshake and
fresh B egress after A loss, ledger settlement and cleanup. This single bounded
drill supports the fix but cannot prove it was the only cause of prior failures.
It does not establish provider-chain independence beyond the approved
origin-ASN distinction and does not claim automatic failover.

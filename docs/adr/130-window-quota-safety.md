# ADR-130 — Window and quota safety under the new lifecycle

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2). No backend change.

## Invariants (all kept, re-checked against the new code)
1. Native never extends a window. `WindowDeadline.advance` only accepts the next generation of
   the same connection and runtime with a later end, from a server answer (Dart `advanceWindow`
   or the native renewer's validated GET). Unchanged.
2. Native never reuses an expired window. After `enforceDeadline()` stops the tunnel, recovery
   only starts the restore engine, whose `connect()` reconciles with the API (revokes stale
   connections) and creates a **new** window through the normal create/validate path.
3. No overlapping windows: the restore engine and the UI never run their reconnect at the same
   time — native starts the restore engine only when no UI engine is attached, and the UI adopts
   or ends the restore engine on attach (ADR-121). The server still refuses a second live
   connection per device.
4. Quota: every new window is created by the API with its own reservation; a terminal quota code
   (`insufficient_quota`, `quota_exhausted`) clears intent, so the background never loops on it.
5. Delayed callbacks: the deadline is compared with `elapsedRealtime` on every event (ADR-126);
   a renewal result arriving after the deadline is refused by `WindowDeadline.advance`
   (`now >= deadline`).
6. Credentials: the native side still holds only the connection-scoped renew token in memory;
   intent is a boolean in `SharedPreferences`; no key material is persisted beyond the existing
   vault (device key).

## Window semantics
The server contract (`window_service.py`, `window_routes.py`, `window_runtime.py`,
`packages/contracts/openapi.json`) is unchanged. No new server calls were added.
